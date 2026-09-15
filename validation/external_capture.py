"""Structural preflight for independently acquired RF/network capture studies.

Passing this check establishes format, alignment, and split integrity only. It
cannot authenticate a claimed real-world observer or replace source review.
"""

from __future__ import annotations

import csv
from bisect import bisect_left
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "flare_external_capture_v1"
PROTOCOL_VERSION = "external_rf_network_v1"
ROLES = {"development", "held_out"}
CATEGORIES = {"real_network", "hardware"}
ATTACK_CLASSES = {
    "normal", "communication_jamming", "dos", "network_spoofing",
    "telemetry_spoofing", "traffic_insider",
}
REQUIRED_COLUMNS = {
    "rf": {
        "timestamp_utc", "drone_id", "rssi", "pdr", "sinr", "latency",
        "packet_loss",
    },
    "network": {
        "timestamp_utc", "drone_id", "observed_source_mac",
        "controller_rx_packets", "controller_tx_packets", "controller_drops",
        "controller_errors", "control_messages",
    },
    "labels": {
        "timestamp_utc", "drone_id", "attack_class", "annotation_origin",
    },
}
MAC_PATTERN = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid UTC timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"timestamp must be in UTC: {value!r}")
    return parsed


def _number(value: str, name: str, *, lower: float | None = None,
            upper: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if lower is not None and number < lower or upper is not None and number > upper:
        raise ValueError(f"{name} outside permitted range: {number}")
    return number


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _capture_file(manifest_path: Path, name: str, record: Any) -> tuple[Path, str]:
    if not isinstance(record, dict):
        raise ValueError(f"files.{name} must contain path and sha256")
    relative = Path(_require_text(record.get("path"), f"files.{name}.path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"files.{name}.path must remain inside the capture directory")
    base = manifest_path.parent.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError(f"files.{name}.path is absent or escapes the capture directory")
    expected = _require_text(record.get("sha256"), f"files.{name}.sha256").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"files.{name}.sha256 must be a SHA-256 digest")
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"files.{name} SHA-256 mismatch")
    return path, actual


def _read_rows(path: Path, name: str, capture_id: str) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError(f"{capture_id}/{name} has duplicate CSV columns")
        columns = set(headers)
        missing = REQUIRED_COLUMNS[name] - columns
        if missing:
            raise ValueError(f"{capture_id}/{name} missing columns: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{capture_id}/{name} has no rows")
    previous: dict[str, datetime] = {}
    previous_counters: dict[str, dict[str, int]] = {}
    for index, row in enumerate(rows, start=2):
        if None in row or any(value is None or not str(value).strip() for value in row.values()):
            raise ValueError(f"{capture_id}/{name} row {index} is malformed or incomplete")
        drone_id = _require_text(row.get("drone_id"), f"{name} row {index} drone_id")
        observed_at = _timestamp(_require_text(
            row.get("timestamp_utc"), f"{name} row {index} timestamp_utc"
        ))
        if drone_id in previous and observed_at <= previous[drone_id]:
            raise ValueError(f"{capture_id}/{name} has duplicate or unordered timestamps for {drone_id}")
        previous[drone_id] = observed_at
        if name == "rf":
            _number(row["rssi"], "rssi")
            _number(row["pdr"], "pdr", lower=0, upper=1)
            _number(row["sinr"], "sinr")
            _number(row["latency"], "latency", lower=0)
            _number(row["packet_loss"], "packet_loss", lower=0, upper=1)
        elif name == "network":
            mac = _require_text(row["observed_source_mac"], "observed_source_mac")
            if not MAC_PATTERN.fullmatch(mac):
                raise ValueError(f"{capture_id}/network has malformed observed_source_mac")
            for column in (
                "controller_rx_packets", "controller_tx_packets", "controller_drops",
                "controller_errors", "control_messages",
            ):
                value = _number(row[column], column, lower=0)
                if not value.is_integer():
                    raise ValueError(f"{capture_id}/network {column} must be a count")
                if column != "control_messages":
                    previous_value = previous_counters.get(drone_id, {}).get(column)
                    if previous_value is not None and value < previous_value:
                        raise ValueError(
                            f"{capture_id}/network {column} reset for {drone_id}; "
                            "split captures at counter resets"
                        )
                    previous_counters.setdefault(drone_id, {})[column] = int(value)
        else:
            if row["attack_class"] not in ATTACK_CLASSES:
                raise ValueError(f"{capture_id}/labels has unsupported attack_class")
            origin = _require_text(row["annotation_origin"], "annotation_origin")
            if origin.lower() in {"scenario", "scenario_injection", "simulation_profile"}:
                raise ValueError(f"{capture_id}/labels uses injected scenario as annotation origin")
    return rows


def _aligned_lag_ms(
    primary: list[dict[str, str]], other: list[dict[str, str]],
    capture_id: str, other_name: str, tolerance_ms: float,
) -> float:
    by_drone: dict[str, list[datetime]] = {}
    for row in other:
        by_drone.setdefault(row["drone_id"], []).append(_timestamp(row["timestamp_utc"]))
    max_lag = 0.0
    for row in primary:
        drone_id = row["drone_id"]
        stamps = by_drone.get(drone_id)
        if not stamps:
            raise ValueError(f"{capture_id}/{other_name} has no rows for {drone_id}")
        observed_at = _timestamp(row["timestamp_utc"])
        position = bisect_left(stamps, observed_at)
        candidates = stamps[max(0, position - 1):position + 1]
        lag = min(abs((observed_at - stamp).total_seconds()) * 1000 for stamp in candidates)
        if lag > tolerance_ms:
            raise ValueError(
                f"{capture_id}/{other_name} is not aligned for {drone_id}: "
                f"{lag:.1f} ms exceeds {tolerance_ms:.1f} ms"
            )
        max_lag = max(max_lag, lag)
    return max_lag


def _preflight_capture(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported external capture manifest schema")
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported external capture protocol")
    capture_id = _require_text(manifest.get("capture_id"), "capture_id")
    campaign_id = _require_text(manifest.get("campaign_id"), "campaign_id")
    role = manifest.get("role")
    if role not in ROLES:
        raise ValueError("capture role must be development or held_out")
    category = manifest.get("evidence_category")
    if category not in CATEGORIES:
        raise ValueError("external capture category must be real_network or hardware")
    acquisition = manifest.get("acquisition")
    if not isinstance(acquisition, dict):
        raise ValueError("acquisition metadata is required")
    for key in (
        "rf_observer", "network_observer", "clock_sync_method", "legal_basis",
        "source_reviewer", "topology_description",
    ):
        _require_text(acquisition.get(key), f"acquisition.{key}")
    if acquisition["rf_observer"] == acquisition["network_observer"]:
        raise ValueError("RF and network observer identifiers must be distinct")
    if acquisition.get("independent_observers") is not True:
        raise ValueError("RF and network observers must be declared independent")
    capture_start = _timestamp(_require_text(
        acquisition.get("capture_start_utc"), "acquisition.capture_start_utc"
    ))
    capture_end = _timestamp(_require_text(
        acquisition.get("capture_end_utc"), "acquisition.capture_end_utc"
    ))
    if capture_end <= capture_start:
        raise ValueError("capture_end_utc must follow capture_start_utc")
    tolerance_ms = _number(
        acquisition.get("timestamp_tolerance_ms"), "timestamp_tolerance_ms",
        lower=0, upper=3000,
    )
    offset_ms = _number(
        acquisition.get("measured_clock_offset_ms"), "measured_clock_offset_ms"
    )
    if abs(offset_ms) > tolerance_ms:
        raise ValueError("measured clock offset exceeds alignment tolerance")
    _number(acquisition.get("clock_drift_ppm"), "clock_drift_ppm", lower=0)
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("files metadata is required")
    paths_and_hashes = {
        name: _capture_file(manifest_path, name, files.get(name))
        for name in REQUIRED_COLUMNS
    }
    rows = {
        name: _read_rows(path, name, capture_id)
        for name, (path, _digest) in paths_and_hashes.items()
    }
    for name, values in rows.items():
        for row in values:
            timestamp = _timestamp(row["timestamp_utc"])
            if not capture_start <= timestamp <= capture_end:
                raise ValueError(f"{capture_id}/{name} timestamp lies outside acquisition interval")
    rf_drone_ids = {row["drone_id"] for row in rows["rf"]}
    if not rf_drone_ids.issubset({row["drone_id"] for row in rows["network"]}):
        raise ValueError("network evidence does not cover all RF drones")
    if not rf_drone_ids.issubset({row["drone_id"] for row in rows["labels"]}):
        raise ValueError("independent labels do not cover all RF drones")
    network_lag = _aligned_lag_ms(
        rows["rf"], rows["network"], capture_id, "network", tolerance_ms,
    )
    label_lag = _aligned_lag_ms(
        rows["rf"], rows["labels"], capture_id, "labels", tolerance_ms,
    )
    fingerprint_payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "capture_id": capture_id,
        "campaign_id": campaign_id,
        "role": role,
        "evidence_category": category,
        "timestamp_tolerance_ms": tolerance_ms,
        "files": {name: digest for name, (_path, digest) in paths_and_hashes.items()},
    }
    fingerprint = hashlib.sha256(json.dumps(
        fingerprint_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {
        "capture_id": capture_id,
        "campaign_id": campaign_id,
        "role": role,
        "claimed_evidence_category": category,
        "rows": {name: len(values) for name, values in rows.items()},
        "drone_ids": sorted(rf_drone_ids),
        "attack_classes": (
            sorted({row["attack_class"] for row in rows["labels"]})
            if role == "development" else "sealed_for_held_out_evaluation"
        ),
        "max_network_alignment_lag_ms": network_lag,
        "max_label_alignment_lag_ms": label_lag,
        "dataset_fingerprint": fingerprint,
        "file_hashes": {
            name: digest for name, (_path, digest) in paths_and_hashes.items()
        },
        "source_review_required": True,
    }


def preflight_study(manifest_paths: list[Path]) -> dict[str, Any]:
    """Check format/alignment and disjoint development versus held-out campaigns."""
    if len(manifest_paths) < 2:
        raise ValueError("study requires separate development and held-out captures")
    captures = [_preflight_capture(path.resolve()) for path in manifest_paths]
    ids = [capture["capture_id"] for capture in captures]
    if len(ids) != len(set(ids)):
        raise ValueError("capture_id is reused across the study")
    development = {capture["campaign_id"] for capture in captures if capture["role"] == "development"}
    held_out = {capture["campaign_id"] for capture in captures if capture["role"] == "held_out"}
    if not development or not held_out or development & held_out:
        raise ValueError("development and held-out captures require disjoint campaign IDs")
    for name in ("rf", "network"):
        development_hashes = {
            capture["file_hashes"][name] for capture in captures
            if capture["role"] == "development"
        }
        held_out_hashes = {
            capture["file_hashes"][name] for capture in captures
            if capture["role"] == "held_out"
        }
        if development_hashes & held_out_hashes:
            raise ValueError(f"development and held-out {name} files are identical")
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "preflight_status": "structurally_ready_source_unverified",
        "validation_category": "structure_only",
        "source_review_required": True,
        "captures": captures,
    }
