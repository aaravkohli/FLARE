"""Regression coverage for temporal FL data and group-disjoint evaluation."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pandas as pd
import pytest

import datasets.download as dataset_download
import api.server as api_server
from fl.checkpoint import (
    FLCheckpointCompatibilityError,
    validate_fl_checkpoint_metadata,
    write_fl_checkpoint_metadata,
)
from fl.client import load_local_data
from fl.data import (
    TEMPORAL_DATA_DEFINITION,
    build_temporal_windows,
    split_by_sequence_group,
)
from fl.distillation import generate_proxy_dataset
from simulation.generator import metrics_to_tensor


MODEL_CONFIG = {
    "dropout": 0.2,
    "hidden_size": 64,
    "input_features": 5,
    "num_attack_classes": 5,
    "num_layers": 2,
    "num_paths": 3,
    "sequence_len": 3,
}
DATA_CONFIG = {"sequence_stride": 1}


def _row(
    group: str,
    order: int,
    marker: float,
    *,
    jammed: int = 0,
    path: str = "direct",
) -> dict:
    return {
        "rssi": marker,
        "pdr": 0.8,
        "sinr": 0.7,
        "latency": 0.2,
        "packet_loss": 0.1,
        "jammed": jammed,
        "attack_class": jammed,
        "source": "test",
        "drone_id": "drone_1",
        "path": path,
        "sequence_group": group,
        "sequence_index": order,
    }


def test_temporal_windows_sort_frames_and_label_the_final_observation():
    dataframe = pd.DataFrame([
        _row("capture-a", 2, 0.3, jammed=1),
        _row("capture-a", 0, 0.1),
        _row("capture-a", 3, 0.4, jammed=1),
        _row("capture-a", 1, 0.2),
    ])

    windows = build_temporal_windows(dataframe, sequence_len=3)

    assert len(windows) == 2
    assert windows.features[0, :, 0].tolist() == pytest.approx([0.1, 0.2, 0.3])
    assert windows.features[1, :, 0].tolist() == pytest.approx([0.2, 0.3, 0.4])
    assert windows.threat_labels[:, 0].tolist() == [1.0, 1.0]
    assert windows.end_order == (2, 3)


def test_temporal_windows_never_cross_capture_or_path_boundaries():
    rows = []
    for group, marker, path in (
        ("capture-a", 0.1, "direct"),
        ("capture-b", 0.7, "direct"),
        ("capture-a", 0.4, "mesh"),
    ):
        rows.extend(_row(group, order, marker + order * 0.01, path=path) for order in range(3))

    windows = build_temporal_windows(pd.DataFrame(rows), sequence_len=3)

    assert len(windows) == 3
    assert len(set(windows.group_ids)) == 3
    for window in windows.features:
        assert float(window[:, 0].max() - window[:, 0].min()) == pytest.approx(
            0.02, abs=1e-6
        )


def test_group_split_has_no_capture_leakage_and_retains_both_classes():
    rows = []
    for group_index in range(20):
        label = group_index % 2
        rows.extend(
            _row(f"capture-{group_index}", order, 0.2 + label * 0.5, jammed=label)
            for order in range(4)
        )
    dataframe = pd.DataFrame(rows)

    train, test = split_by_sequence_group(
        dataframe,
        test_fraction=0.2,
        random_state=9,
    )

    train_groups = set(train["sequence_group"])
    test_groups = set(test["sequence_group"])
    assert train_groups.isdisjoint(test_groups)
    assert set(train["jammed"]) == {0, 1}
    assert set(test["jammed"]) == {0, 1}
    assert len(test) / len(dataframe) == pytest.approx(0.2)


def test_synthetic_client_sequences_contain_temporal_variation():
    features, _, _ = load_local_data("drone_1", seq_len=6, num_samples=12)

    per_sequence_change = (features[:, 1:, :] - features[:, :-1, :]).abs().sum(dim=(1, 2))
    assert (per_sequence_change > 0).all()


def test_distillation_proxy_sequences_contain_temporal_variation():
    features, _ = generate_proxy_dataset(n_samples=12, seq_len=6, seed=11)

    per_sequence_change = (features[:, 1:, :] - features[:, :-1, :]).abs().sum(dim=(1, 2))
    assert (per_sequence_change > 0).all()


def test_generated_fallback_keeps_one_drone_and_path_per_trace(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(dataset_download, "_RAW", tmp_path)

    dataset_download.generate_synthetic_fallback(n_samples=120, overwrite=True)
    dataframe = pd.read_csv(tmp_path / "synthetic_fallback.csv")

    grouped = dataframe.groupby("sequence_group")
    assert grouped["drone_id"].nunique().max() == 1
    assert grouped["path"].nunique().max() == 1
    assert dataframe.groupby("sequence_group")["sequence_index"].apply(
        lambda values: values.tolist() == list(range(len(values)))
    ).all()


def _snapshot(rssi: float) -> dict:
    return {
        "paths": [
            {
                "path_id": path_name,
                "rssi": rssi - path_index,
                "pdr": 0.9,
                "sinr": 20.0,
                "latency": 20.0,
                "packet_loss": 0.02,
            }
            for path_index, path_name in enumerate(
                ["mesh", "satellite", "direct"]
            )
        ]
    }


def test_runtime_tensor_uses_chronological_history_and_canonical_path_order():
    history = [_snapshot(-90.0), _snapshot(-80.0), _snapshot(-70.0)]

    sequences = metrics_to_tensor(history[-1], seq_len=3, history=history)

    assert len(sequences) == 3
    # Direct is returned first despite each snapshot listing it last. Its path
    # offset is two dB in this fixture: -92, -82, -72 -> 0.28, 0.38, 0.48.
    assert sequences[0][:, 0].tolist() == pytest.approx([0.28, 0.38, 0.48])
    assert not np.array_equal(sequences[0][0], sequences[0][-1])


def test_fl_checkpoint_metadata_round_trip(tmp_path):
    checkpoint = tmp_path / "fl_model.pth"
    checkpoint.write_bytes(b"temporal-model")

    write_fl_checkpoint_metadata(
        checkpoint,
        model_config=MODEL_CONFIG,
        data_config=DATA_CONFIG,
        federation_round=7,
    )
    metadata = validate_fl_checkpoint_metadata(
        checkpoint,
        model_config=MODEL_CONFIG,
        data_config=DATA_CONFIG,
    )

    assert metadata["data_definition"] == TEMPORAL_DATA_DEFINITION
    assert metadata["federation_round"] == 7


def test_fl_checkpoint_without_temporal_metadata_is_rejected(tmp_path):
    checkpoint = tmp_path / "legacy_fl_model.pth"
    checkpoint.write_bytes(b"repeated-row-model")

    with pytest.raises(FLCheckpointCompatibilityError, match="metadata not found"):
        validate_fl_checkpoint_metadata(
            checkpoint,
            model_config=MODEL_CONFIG,
            data_config=DATA_CONFIG,
        )


def test_fl_checkpoint_with_old_data_definition_is_rejected(tmp_path):
    checkpoint = tmp_path / "old_data_model.pth"
    checkpoint.write_bytes(b"old-data-model")
    sidecar = write_fl_checkpoint_metadata(
        checkpoint,
        model_config=MODEL_CONFIG,
        data_config=DATA_CONFIG,
        federation_round=2,
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["data_definition"] = "repeated_rows_v0"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(FLCheckpointCompatibilityError, match="data_definition"):
        validate_fl_checkpoint_metadata(
            checkpoint,
            model_config=MODEL_CONFIG,
            data_config=DATA_CONFIG,
        )


def test_jam_visibility_waits_for_a_new_matching_canonical_event(monkeypatch):
    previous = {
        "drone_1": {
            "event_id": "run:1:drone_1",
            "timestamp": api_server.time.time(),
            "telemetry": {"ew_status": {"active_attack": None}},
        }
    }
    calls = 0

    async def latest_events():
        nonlocal calls
        calls += 1
        if calls == 1:
            return previous
        return {
            "drone_1": {
                "event_id": "run:2:drone_1",
                "timestamp": api_server.time.time(),
                "telemetry": {"ew_status": {"active_attack": "spot"}},
            }
        }

    monkeypatch.setattr(api_server, "_latest_decision_events", latest_events)

    visible = asyncio.run(
        api_server._wait_for_jam_visibility(
            ["drone_1"],
            previous,
            "spot",
            timeout_s=0.2,
        )
    )

    assert visible is True
    assert calls == 2
