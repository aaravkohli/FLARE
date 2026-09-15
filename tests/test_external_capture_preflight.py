"""Structural checks must never be mistaken for real-source authentication."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from validation.external_capture import preflight_study


def _csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def _capture(root: Path, capture_id: str, campaign_id: str, role: str) -> Path:
    directory = root / capture_id
    directory.mkdir()
    timestamp = "06:00:00" if role == "development" else "06:01:00"
    _csv(directory / "rf.csv", [
        "timestamp_utc", "drone_id", "rssi", "pdr", "sinr", "latency", "packet_loss",
    ], [[f"2026-09-15T{timestamp}Z", "drone_1", "-72", "0.95", "12", "25", "0.05"]])
    _csv(directory / "network.csv", [
        "timestamp_utc", "drone_id", "observed_source_mac",
        "controller_rx_packets", "controller_tx_packets", "controller_drops",
        "controller_errors", "control_messages",
    ], [[f"2026-09-15T{timestamp}.200Z", "drone_1", "02:00:00:00:00:01",
         "100", "96", "4", "0", "2"]])
    _csv(directory / "labels.csv", [
        "timestamp_utc", "drone_id", "attack_class", "annotation_origin",
    ], [[f"2026-09-15T{timestamp}.100Z", "drone_1", "normal", "reviewed_packet_capture"]])
    manifest = {
        "schema_version": "flare_external_capture_v1",
        "protocol_version": "external_rf_network_v1",
        "capture_id": capture_id,
        "campaign_id": campaign_id,
        "role": role,
        "evidence_category": "real_network",
        "acquisition": {
            "rf_observer": "test-only-receiver",
            "network_observer": "test-only-controller",
            "independent_observers": True,
            "clock_sync_method": "test-only-clock",
            "timestamp_tolerance_ms": 500,
            "capture_start_utc": f"2026-09-15T{timestamp}Z",
            "capture_end_utc": (
                "2026-09-15T06:00:10Z" if role == "development"
                else "2026-09-15T06:01:10Z"
            ),
            "measured_clock_offset_ms": 50,
            "clock_drift_ppm": 0.5,
            "legal_basis": "test-only-placeholder",
            "source_reviewer": "test-only-reviewer",
            "topology_description": "test-only-topology",
        },
        "files": {
            name: {
                "path": f"{name}.csv",
                "sha256": hashlib.sha256((directory / f"{name}.csv").read_bytes()).hexdigest(),
            }
            for name in ("rf", "network", "labels")
        },
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _update(manifest_path: Path, modifier) -> None:
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    modifier(value)
    manifest_path.write_text(json.dumps(value), encoding="utf-8")


def test_disjoint_capture_preflight_is_structure_only(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    report = preflight_study([development, held_out])
    assert report["preflight_status"] == "structurally_ready_source_unverified"
    assert report["validation_category"] == "structure_only"
    assert report["source_review_required"] is True
    assert report["captures"][0]["rows"] == {"rf": 1, "network": 1, "labels": 1}
    assert report["captures"][0]["three_path_rf_network_inputs_structurally_present"] is False
    assert report["captures"][0]["offline_model_replay_ready"] is False
    assert report["captures"][0]["max_network_alignment_lag_ms"] == 200.0
    assert len(report["captures"][0]["dataset_fingerprint"]) == 64
    assert report["captures"][1]["attack_classes"] == "sealed_for_held_out_evaluation"


def test_single_capture_and_campaign_leakage_fail(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "same-campaign", "development")
    held_out = _capture(tmp_path, "held", "same-campaign", "held_out")
    with pytest.raises(ValueError, match="separate development and held-out"):
        preflight_study([development])
    with pytest.raises(ValueError, match="disjoint campaign"):
        preflight_study([development, held_out])


def test_identical_rf_file_across_splits_fails(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    for name in ("rf", "network", "labels"):
        destination = held_out.parent / f"{name}.csv"
        destination.write_bytes((development.parent / f"{name}.csv").read_bytes())
        _update(held_out, lambda value, name=name, destination=destination: value["files"][name].update(
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest()
        ))
    _update(held_out, lambda value: value["acquisition"].update(
        capture_start_utc="2026-09-15T06:00:00Z",
        capture_end_utc="2026-09-15T06:00:10Z",
    ))
    with pytest.raises(ValueError, match="identical"):
        preflight_study([development, held_out])


def test_stale_file_hash_fails(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    with (development.parent / "rf.csv").open("a", encoding="utf-8") as handle:
        handle.write("2026-09-15T06:00:01Z,drone_1,-70,0.9,12,30,0.1\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        preflight_study([development, held_out])


def test_claimed_synthetic_or_nonindependent_source_fails(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    _update(development, lambda value: value.update(evidence_category="controlled_simulation"))
    with pytest.raises(ValueError, match="real_network or hardware"):
        preflight_study([development, held_out])
    _update(development, lambda value: value.update(evidence_category="real_network"))
    _update(development, lambda value: value["acquisition"].update(independent_observers=False))
    with pytest.raises(ValueError, match="independent"):
        preflight_study([development, held_out])


def test_observer_identity_and_clock_offset_must_be_valid(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    _update(development, lambda value: value["acquisition"].update(
        network_observer="test-only-receiver"
    ))
    with pytest.raises(ValueError, match="distinct"):
        preflight_study([development, held_out])
    _update(development, lambda value: value["acquisition"].update(
        network_observer="test-only-controller", measured_clock_offset_ms=600
    ))
    with pytest.raises(ValueError, match="offset exceeds"):
        preflight_study([development, held_out])


def test_controller_counter_reset_requires_new_capture(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    network = development.parent / "network.csv"
    with network.open("a", encoding="utf-8") as handle:
        handle.write("2026-09-15T06:00:01Z,drone_1,02:00:00:00:00:01,0,0,0,0,1\n")
    _update(development, lambda value: value["files"]["network"].update(
        sha256=hashlib.sha256(network.read_bytes()).hexdigest()
    ))
    with pytest.raises(ValueError, match="reset"):
        preflight_study([development, held_out])


def test_unaligned_network_and_injected_label_fail(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    network = development.parent / "network.csv"
    network.write_text(network.read_text().replace("06:00:00.200Z", "06:00:04.000Z"))
    _update(development, lambda value: value["files"]["network"].update(
        sha256=hashlib.sha256(network.read_bytes()).hexdigest()
    ))
    with pytest.raises(ValueError, match="not aligned"):
        preflight_study([development, held_out])
    network.write_text(network.read_text().replace("06:00:04.000Z", "06:00:00.200Z"))
    _update(development, lambda value: value["files"]["network"].update(
        sha256=hashlib.sha256(network.read_bytes()).hexdigest()
    ))
    labels = development.parent / "labels.csv"
    labels.write_text(labels.read_text().replace("reviewed_packet_capture", "simulation_profile"))
    _update(development, lambda value: value["files"]["labels"].update(
        sha256=hashlib.sha256(labels.read_bytes()).hexdigest()
    ))
    with pytest.raises(ValueError, match="injected scenario"):
        preflight_study([development, held_out])


def test_cli_exits_nonzero_without_disjoint_captures(tmp_path: Path) -> None:
    development = _capture(tmp_path, "dev", "campaign-A", "development")
    held_out = _capture(tmp_path, "held", "campaign-B", "held_out")
    script = Path(__file__).resolve().parents[1] / "scripts" / "preflight_external_capture.py"
    success = subprocess.run(
        [sys.executable, str(script), str(development), str(held_out)],
        capture_output=True, text=True, check=False,
    )
    assert success.returncode == 0
    assert json.loads(success.stdout)["validation_category"] == "structure_only"
    failed = subprocess.run(
        [sys.executable, str(script), str(development)],
        capture_output=True, text=True, check=False,
    )
    assert failed.returncode == 2
    assert "separate development and held-out" in failed.stderr
