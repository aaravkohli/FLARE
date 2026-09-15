"""Version-2 external capture preflight: replay-shaped inputs, structure only."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from validation.external_capture import _check_v2_windows, preflight_study
from tests.test_external_capture_preflight import _capture as legacy_capture


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _capture(root: Path, capture_id: str, campaign_id: str, role: str) -> Path:
    directory = root / capture_id
    directory.mkdir()
    start = datetime(2026, 9, 15, 6, 0 if role == "development" else 1, tzinfo=timezone.utc)
    rf_rows: list[list[str]] = []
    reported_rows: list[list[str]] = []
    network_rows: list[list[str]] = []
    label_rows: list[list[str]] = []
    for step in range(10):
        end = start + timedelta(seconds=step + 1)
        for path in ("direct", "satellite", "mesh"):
            rf_rows.append([_stamp(end), "drone_1", path, "-72", "0.95", "12", "25", "0.05"])
        reported_rows.append([
            _stamp(end + timedelta(milliseconds=100)), "drone_1", "100", "98", "1.0",
        ])
        network_rows.append([
            _stamp(end + timedelta(milliseconds=200)), "drone_1", "100", "98", "1.0",
            "0", str(100 + step * 100), str(2 + step), "0", "2.0", "100.0",
            "02:00:00:00:00:01", _stamp(end + timedelta(milliseconds=150)),
        ])
        label_rows.append([
            _stamp(end + timedelta(milliseconds=150)), "drone_1", "normal",
            "independently_reviewed_packet_capture",
        ])
    _write_csv(directory / "rf.csv", [
        "timestamp_utc", "drone_id", "path_name", "rssi", "pdr", "sinr",
        "latency", "packet_loss",
    ], rf_rows)
    _write_csv(directory / "reported.csv", [
        "timestamp_utc", "drone_id", "reported_tx_packets",
        "reported_forwarded_packets", "report_window_seconds",
    ], reported_rows)
    _write_csv(directory / "network.csv", [
        "timestamp_utc", "drone_id", "controller_rx_packets",
        "controller_forwarded_packets", "flow_window_seconds",
        "controller_policy_dropped_packets", "controller_port_rx_packets",
        "controller_port_dropped_packets", "controller_port_error_packets",
        "control_messages_per_s", "packet_rate_per_s",
        "observed_source_mac", "identity_observed_at_utc",
    ], network_rows)
    _write_csv(directory / "labels.csv", [
        "timestamp_utc", "drone_id", "attack_class", "annotation_origin",
    ], label_rows)
    (directory / "registry.json").write_text(json.dumps({
        "schema_version": "flare_capture_registry_v1",
        "drones": {"drone_1": {"mac": "02:00:00:00:00:01", "access_port": 5}},
    }), encoding="utf-8")
    files = {
        name: {
            "path": f"{name}.{'json' if name == 'registry' else 'csv'}",
            "sha256": hashlib.sha256(
                (directory / f"{name}.{'json' if name == 'registry' else 'csv'}").read_bytes()
            ).hexdigest(),
        }
        for name in ("rf", "reported", "network", "labels", "registry")
    }
    manifest = {
        "schema_version": "flare_external_capture_v1",
        "protocol_version": "external_rf_network_v2",
        "capture_id": capture_id,
        "campaign_id": campaign_id,
        "role": role,
        "evidence_category": "real_network",
        "acquisition": {
            "rf_observer": "test-only-independent-receiver",
            "participant_observer": "test-only-participant",
            "network_observer": "test-only-controller",
            "independent_observers": True,
            "flow_counter_scope": "sample_window",
            "port_counter_scope": "cumulative_since_switch_start",
            "port_drop_semantics": "ingress_port_receive_drop_error",
            "identity_observation_method": "access_port_packet_in",
            "clock_sync_method": "test-only-clock",
            "timestamp_tolerance_ms": 500,
            "capture_start_utc": _stamp(start),
            "capture_end_utc": _stamp(start + timedelta(seconds=15)),
            "measured_clock_offset_ms": 50,
            "clock_drift_ppm": 0.5,
            "legal_basis": "test-only-placeholder",
            "source_reviewer": "test-only-reviewer",
            "topology_description": "test-only-three-route topology",
        },
        "files": files,
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _rewrite(manifest_path: Path, name: str, change) -> None:
    target = manifest_path.parent / f"{name}.{'json' if name == 'registry' else 'csv'}"
    if name == "registry":
        value = json.loads(target.read_text(encoding="utf-8"))
        change(value)
        target.write_text(json.dumps(value), encoding="utf-8")
    else:
        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = list(reader)
        headers, rows = change(headers, rows)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][name]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _capture(tmp_path, "dev", "campaign-A", "development"),
        _capture(tmp_path, "held", "campaign-B", "held_out"),
    )


def test_v2_preflight_has_three_path_inputs_but_no_source_validation(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    report = preflight_study([development, held_out])
    assert report["protocol_version"] == "external_rf_network_v2"
    assert report["validation_category"] == "structure_only"
    assert report["source_review_required"] is True
    assert report["captures"][0]["rows"] == {
        "rf": 30, "reported": 10, "network": 10, "labels": 10,
    }
    assert report["captures"][0]["three_path_rf_network_inputs_structurally_present"] is True
    assert report["captures"][0]["offline_model_replay_ready"] is False
    assert report["captures"][0]["usable_port_windows_by_drone"] == {"drone_1": 9}
    assert report["captures"][0]["unavailable_evidence_timesteps"] == {
        "controller_missing": 0, "participant_report_missing": 0,
        "joint_missing": 0, "unpaired_controller_observations": 0,
        "controller_identity_missing": 0,
    }
    assert report["captures"][1]["attack_classes"] == "sealed_for_held_out_evaluation"


def test_v2_cli_reports_structure_only_and_no_offline_replay(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "preflight_external_capture.py"
    process = subprocess.run(
        [sys.executable, str(script), str(development), str(held_out)],
        capture_output=True, text=True, check=False,
    )
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["protocol_version"] == "external_rf_network_v2"
    assert payload["validation_category"] == "structure_only"
    assert payload["captures"][0]["offline_model_replay_ready"] is False


def test_v2_cannot_infer_port_loss_window_across_long_poll_gap() -> None:
    reported = [
        {"timestamp_utc": stamp, "drone_id": "drone_1", "report_window_seconds": "1.0"}
        for stamp in ("2026-09-15T06:00:01Z", "2026-09-15T06:00:06Z")
    ]
    network = [
        {"timestamp_utc": stamp, "drone_id": "drone_1", "flow_window_seconds": "1.0"}
        for stamp in ("2026-09-15T06:00:01Z", "2026-09-15T06:00:06Z")
    ]
    usable, unpaired = _check_v2_windows(reported, network, "test-capture", 500)
    assert usable == {"drone_1": 0}
    assert unpaired == 0


def test_v2_missing_independent_evidence_is_counted_not_excluded(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "reported", lambda headers, rows: (
        headers, [row for row in rows if not row["timestamp_utc"].endswith("05.100Z")]
    ))
    _rewrite(development, "network", lambda headers, rows: (
        headers, [row for row in rows if not row["timestamp_utc"].endswith("06.200Z")]
    ))
    report = preflight_study([development, held_out])
    gaps = report["captures"][0]["unavailable_evidence_timesteps"]
    assert gaps["participant_report_missing"] == 1
    assert gaps["controller_missing"] == 1
    assert gaps["joint_missing"] == 2
    assert gaps["unpaired_controller_observations"] == 1
    assert gaps["controller_identity_missing"] == 0
    assert report["captures"][0]["attack_classes"] == ["normal"]


def test_v2_unaligned_observation_is_malformed_not_unavailable(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "network", lambda headers, rows: (
        headers, [
            {**row, "timestamp_utc": "2026-09-15T06:00:10.800Z"}
            if index == 9 else row for index, row in enumerate(rows)
        ]
    ))
    with pytest.raises(ValueError, match="not aligned"):
        preflight_study([development, held_out])


def test_v2_missing_route_or_short_sequence_fails(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "rf", lambda headers, rows: (
        headers, [row for row in rows if not (
            row["path_name"] == "mesh" and row["timestamp_utc"].endswith("01.000Z")
        )]
    ))
    with pytest.raises(ValueError, match="three-path triplet"):
        preflight_study([development, held_out])


def test_v2_requires_ten_complete_timesteps_per_drone(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "rf", lambda headers, rows: (
        headers, [row for row in rows if not row["timestamp_utc"].endswith("10.000Z")]
    ))
    with pytest.raises(ValueError, match="at least 10 timesteps"):
        preflight_study([development, held_out])


def test_v2_counter_reset_and_policy_semantics_fail(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "network", lambda headers, rows: (
        headers, [{**row, "controller_port_rx_packets": "1"} if index == 5 else row
                  for index, row in enumerate(rows)]
    ))
    with pytest.raises(ValueError, match="reset"):
        preflight_study([development, held_out])
    _rewrite(development, "network", lambda headers, rows: (
        headers, [{**row, "controller_port_rx_packets": str(100 + index * 100)}
                  for index, row in enumerate(rows)]
    ))
    manifest = json.loads(development.read_text(encoding="utf-8"))
    manifest["acquisition"]["port_drop_semantics"] = "includes_policy_drop"
    development.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="port_drop_semantics"):
        preflight_study([development, held_out])


def test_v2_identity_pair_freshness_and_label_leakage_fail(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "network", lambda headers, rows: (
        headers, [{**row, "identity_observed_at_utc": ""} if index == 0 else row
                  for index, row in enumerate(rows)]
    ))
    with pytest.raises(ValueError, match="identity needs its observation time"):
        preflight_study([development, held_out])
    _rewrite(development, "network", lambda headers, rows: (
        headers, [{**row, "identity_observed_at_utc": "2026-09-15T05:59:00.000Z"}
                  if index == 0 else row for index, row in enumerate(rows)]
    ))
    with pytest.raises(ValueError, match="not fresh"):
        preflight_study([development, held_out])
    _rewrite(development, "network", lambda headers, rows: (
        headers, [{**row, "identity_observed_at_utc": row["timestamp_utc"]}
                  for row in rows]
    ))
    _rewrite(development, "rf", lambda headers, rows: (
        [*headers, "simulation_profile"],
        [{**row, "simulation_profile": "normal"} for row in rows],
    ))
    with pytest.raises(ValueError, match="label leakage"):
        preflight_study([development, held_out])


def test_v2_absent_packet_in_identity_is_explicitly_counted(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "network", lambda headers, rows: (
        headers, [
            {**row, "observed_source_mac": "", "identity_observed_at_utc": ""}
            if index == 4 else row for index, row in enumerate(rows)
        ]
    ))
    report = preflight_study([development, held_out])
    assert report["captures"][0]["unavailable_evidence_timesteps"][
        "controller_identity_missing"
    ] == 1


def test_v2_report_windows_registry_and_mixed_protocol_fail(tmp_path: Path) -> None:
    development, held_out = _pair(tmp_path)
    _rewrite(development, "reported", lambda headers, rows: (
        headers, [{**row, "report_window_seconds": "2.0"} for row in rows]
    ))
    with pytest.raises(ValueError, match="windows are misaligned"):
        preflight_study([development, held_out])
    _rewrite(development, "reported", lambda headers, rows: (
        headers, [{**row, "report_window_seconds": "1.0"} for row in rows]
    ))
    _rewrite(development, "registry", lambda value: value["drones"].clear())
    with pytest.raises(ValueError, match="frozen drone bindings"):
        preflight_study([development, held_out])
    _rewrite(development, "registry", lambda value: value["drones"].update({
        "drone_1": {"mac": "02:00:00:00:00:01", "access_port": 5}
    }))
    legacy = legacy_capture(tmp_path, "legacy", "campaign-C", "held_out")
    with pytest.raises(ValueError, match="one protocol version"):
        preflight_study([development, legacy])
