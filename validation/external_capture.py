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
PROTOCOL_V2 = "external_rf_network_v2"
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
REQUIRED_COLUMNS_V2 = {
    "rf": {
        "timestamp_utc", "drone_id", "path_name", "rssi", "pdr", "sinr",
        "latency", "packet_loss",
    },
    "reported": {
        "timestamp_utc", "drone_id", "reported_tx_packets",
        "reported_forwarded_packets", "report_window_seconds",
    },
    "network": {
        "timestamp_utc", "drone_id", "controller_rx_packets",
        "controller_forwarded_packets", "flow_window_seconds",
        "controller_policy_dropped_packets", "controller_port_rx_packets",
        "controller_port_dropped_packets", "controller_port_error_packets",
        "control_messages_per_s", "packet_rate_per_s",
        "observed_source_mac", "identity_observed_at_utc",
    },
    "labels": REQUIRED_COLUMNS["labels"],
    "registry": set(),
}
ROUTE_NAMES = {"direct", "satellite", "mesh"}
LEAKAGE_COLUMNS = {
    "simulation_profile", "ground_truth", "attack_label", "attack_class",
    "jammer_profile", "injected_scenario",
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


def _read_rows_v2(path: Path, name: str, capture_id: str) -> list[dict[str, str]]:
    """Validate replay-shaped CSVs without inspecting sealed label outcomes."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError(f"{capture_id}/{name} has duplicate CSV columns")
        missing = REQUIRED_COLUMNS_V2[name] - set(headers)
        if missing:
            raise ValueError(f"{capture_id}/{name} missing columns: {sorted(missing)}")
        if name != "labels" and LEAKAGE_COLUMNS & set(headers):
            raise ValueError(f"{capture_id}/{name} contains detector-label leakage columns")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{capture_id}/{name} has no rows")
    previous: dict[tuple[str, str], datetime] = {}
    previous_port_counters: dict[str, dict[str, int]] = {}
    triplets: dict[tuple[str, datetime], set[str]] = {}
    for index, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{capture_id}/{name} row {index} is malformed")
        optional_blanks = (
            {"observed_source_mac", "identity_observed_at_utc"}
            if name == "network" else set()
        )
        if any(not str(value).strip() for column, value in row.items()
               if column not in optional_blanks):
            raise ValueError(f"{capture_id}/{name} row {index} is incomplete")
        drone_id = _require_text(row.get("drone_id"), f"{name} row {index} drone_id")
        observed_at = _timestamp(_require_text(
            row.get("timestamp_utc"), f"{name} row {index} timestamp_utc"
        ))
        path_name = row["path_name"] if name == "rf" else ""
        key = (drone_id, path_name)
        if key in previous and observed_at <= previous[key]:
            raise ValueError(f"{capture_id}/{name} has duplicate or unordered timestamps for {key}")
        previous[key] = observed_at
        if name == "rf":
            if path_name not in ROUTE_NAMES:
                raise ValueError(f"{capture_id}/rf has unsupported path_name")
            triplets.setdefault((drone_id, observed_at), set()).add(path_name)
            _number(row["rssi"], "rssi")
            _number(row["pdr"], "pdr", lower=0, upper=1)
            _number(row["sinr"], "sinr")
            _number(row["latency"], "latency", lower=0)
            _number(row["packet_loss"], "packet_loss", lower=0, upper=1)
        elif name == "reported":
            reported_tx = _number(row["reported_tx_packets"], "reported_tx_packets", lower=0)
            reported_forwarded = _number(
                row["reported_forwarded_packets"], "reported_forwarded_packets", lower=0
            )
            if not reported_tx.is_integer() or not reported_forwarded.is_integer() or (
                reported_forwarded > reported_tx
            ):
                raise ValueError(f"{capture_id}/reported packet-window counts are invalid")
            _number(row["report_window_seconds"], "report_window_seconds", lower=1e-9, upper=3)
        elif name == "network":
            received = _number(row["controller_rx_packets"], "controller_rx_packets", lower=0)
            forwarded = _number(
                row["controller_forwarded_packets"], "controller_forwarded_packets", lower=0
            )
            policy = _number(
                row["controller_policy_dropped_packets"],
                "controller_policy_dropped_packets", lower=0,
            )
            if any(not value.is_integer() for value in (received, forwarded, policy)) or (
                forwarded > received or policy > received
            ):
                raise ValueError(f"{capture_id}/network flow-window counts are invalid")
            _number(row["flow_window_seconds"], "flow_window_seconds", lower=1e-9, upper=3)
            for column in (
                "controller_port_rx_packets", "controller_port_dropped_packets",
                "controller_port_error_packets",
            ):
                value = _number(row[column], column, lower=0)
                if not value.is_integer():
                    raise ValueError(f"{capture_id}/network {column} must be a cumulative count")
                previous_value = previous_port_counters.get(drone_id, {}).get(column)
                if previous_value is not None and value < previous_value:
                    raise ValueError(
                        f"{capture_id}/network {column} reset for {drone_id}; "
                        "split captures at port counter resets"
                    )
                previous_port_counters.setdefault(drone_id, {})[column] = int(value)
            _number(row["control_messages_per_s"], "control_messages_per_s", lower=0)
            _number(row["packet_rate_per_s"], "packet_rate_per_s", lower=0)
            mac = row["observed_source_mac"].strip()
            identity_at = row["identity_observed_at_utc"].strip()
            if bool(mac) != bool(identity_at):
                raise ValueError(f"{capture_id}/network source identity needs its observation time")
            if mac:
                if not MAC_PATTERN.fullmatch(mac):
                    raise ValueError(f"{capture_id}/network has malformed observed_source_mac")
                identity_time = _timestamp(identity_at)
                identity_age = (observed_at - identity_time).total_seconds()
                if not -0.5 <= identity_age <= 3.0:
                    raise ValueError(f"{capture_id}/network source identity is not fresh")
        elif name == "labels":
            if row["attack_class"] not in ATTACK_CLASSES:
                raise ValueError(f"{capture_id}/labels has unsupported attack_class")
            origin = _require_text(row["annotation_origin"], "annotation_origin")
            if origin.lower() in {"scenario", "scenario_injection", "simulation_profile"}:
                raise ValueError(f"{capture_id}/labels uses injected scenario as annotation origin")
    if name == "rf":
        if any(paths != ROUTE_NAMES for paths in triplets.values()):
            raise ValueError(f"{capture_id}/rf is missing a synchronized three-path triplet")
        by_drone: dict[str, int] = {}
        for drone_id, _timestamp_value in triplets:
            by_drone[drone_id] = by_drone.get(drone_id, 0) + 1
        if any(count < 10 for count in by_drone.values()):
            raise ValueError(f"{capture_id}/rf needs at least 10 timesteps per drone")
    return rows


def _read_registry_v2(path: Path, capture_id: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "flare_capture_registry_v1":
        raise ValueError(f"{capture_id}/registry has unsupported schema")
    drones = payload.get("drones")
    if not isinstance(drones, dict) or not drones:
        raise ValueError(f"{capture_id}/registry needs frozen drone bindings")
    macs: set[str] = set()
    ports: set[int] = set()
    for drone_id, binding in drones.items():
        _require_text(drone_id, "registry drone_id")
        if not isinstance(binding, dict):
            raise ValueError(f"{capture_id}/registry binding is malformed")
        mac = _require_text(binding.get("mac"), "registry.mac").lower()
        if not MAC_PATTERN.fullmatch(mac) or mac in macs:
            raise ValueError(f"{capture_id}/registry MAC is invalid or reused")
        port = binding.get("access_port")
        if not isinstance(port, int) or isinstance(port, bool) or port <= 0 or port in ports:
            raise ValueError(f"{capture_id}/registry access port is invalid or reused")
        macs.add(mac)
        ports.add(port)
    return drones


def _aligned_lag_ms(
    primary: list[dict[str, str]], other: list[dict[str, str]],
    capture_id: str, other_name: str, tolerance_ms: float,
) -> float:
    by_drone: dict[str, list[datetime]] = {}
    for row in other:
        by_drone.setdefault(row["drone_id"], []).append(_timestamp(row["timestamp_utc"]))
    for stamps in by_drone.values():
        stamps.sort()
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


def _v2_alignment_profile(
    rf: list[dict[str, str]], source: list[dict[str, str]],
    capture_id: str, source_name: str, tolerance_ms: float,
) -> tuple[float, set[tuple[str, datetime]]]:
    """Count missing source samples without excluding their RF timesteps."""
    by_drone: dict[str, list[datetime]] = {}
    for row in source:
        by_drone.setdefault(row["drone_id"], []).append(_timestamp(row["timestamp_utc"]))
    for stamps in by_drone.values():
        stamps.sort()
    rf_steps = {
        (row["drone_id"], _timestamp(row["timestamp_utc"])) for row in rf
    }
    missing: set[tuple[str, datetime]] = set()
    max_lag = 0.0
    for drone_id, observed_at in rf_steps:
        stamps = by_drone.get(drone_id, [])
        if not stamps:
            missing.add((drone_id, observed_at))
            continue
        position = bisect_left(stamps, observed_at)
        candidates = stamps[max(0, position - 1):position + 1]
        lag = min(abs((observed_at - stamp).total_seconds()) * 1000 for stamp in candidates)
        if lag > tolerance_ms:
            missing.add((drone_id, observed_at))
        else:
            max_lag = max(max_lag, lag)
    # An existing source observation must belong to a synchronized RF step;
    # missing source observations above are operational UNAVAILABLE, not errors.
    _aligned_lag_ms(source, rf, capture_id, "rf", tolerance_ms)
    return max_lag, missing


def _check_v2_windows(
    reported: list[dict[str, str]], network: list[dict[str, str]],
    capture_id: str, tolerance_ms: float, rf_drone_ids: set[str] | None = None,
) -> tuple[dict[str, int], int]:
    """Check paired windows; leave missing pairs and port deltas visible."""
    by_report: dict[str, list[tuple[datetime, dict[str, str]]]] = {}
    by_network: dict[str, list[datetime]] = {}
    for row in reported:
        by_report.setdefault(row["drone_id"], []).append(
            (_timestamp(row["timestamp_utc"]), row)
        )
    for row in network:
        by_network.setdefault(row["drone_id"], []).append(
            _timestamp(row["timestamp_utc"])
        )
    usable: dict[str, int] = {drone_id: 0 for drone_id in (rf_drone_ids or set())}
    for drone_id, stamps in by_network.items():
        stamps.sort()
        usable[drone_id] = sum(
            0 < (current - previous).total_seconds() <= 3.0
            for previous, current in zip(stamps, stamps[1:])
        )
    for drone_id, entries in by_report.items():
        entries.sort(key=lambda item: item[0])
    by_report_stamps = {
        drone_id: [item[0] for item in entries]
        for drone_id, entries in by_report.items()
    }
    unpaired_network = 0
    for row in network:
        drone_id = row["drone_id"]
        entries = by_report.get(drone_id)
        if not entries:
            unpaired_network += 1
            continue
        end = _timestamp(row["timestamp_utc"])
        position = bisect_left(by_report_stamps[drone_id], end)
        candidates = entries[max(0, position - 1):position + 1]
        matched_end, matched_row = min(
            candidates, key=lambda item: abs((end - item[0]).total_seconds())
        )
        if abs((end - matched_end).total_seconds()) * 1000 > tolerance_ms:
            unpaired_network += 1
            continue
        flow_start = end.timestamp() - float(row["flow_window_seconds"])
        report_start = matched_end.timestamp() - float(matched_row["report_window_seconds"])
        if abs(flow_start - report_start) * 1000 > tolerance_ms:
            raise ValueError(f"{capture_id}/reported and network windows are misaligned for {drone_id}")
    return usable, unpaired_network


def _preflight_capture(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported external capture manifest schema")
    protocol = manifest.get("protocol_version")
    if protocol not in {PROTOCOL_VERSION, PROTOCOL_V2}:
        raise ValueError("unsupported external capture protocol")
    version_two = protocol == PROTOCOL_V2
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
    if version_two:
        participant = _require_text(
            acquisition.get("participant_observer"), "acquisition.participant_observer"
        )
        if participant in {acquisition["rf_observer"], acquisition["network_observer"]}:
            raise ValueError("participant, RF, and controller observer identifiers must be distinct")
        for key, expected in (
            ("flow_counter_scope", "sample_window"),
            ("port_counter_scope", "cumulative_since_switch_start"),
            ("port_drop_semantics", "ingress_port_receive_drop_error"),
            ("identity_observation_method", "access_port_packet_in"),
        ):
            if acquisition.get(key) != expected:
                raise ValueError(f"acquisition.{key} is incompatible with protocol v2")
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
    required_files = REQUIRED_COLUMNS_V2 if version_two else REQUIRED_COLUMNS
    paths_and_hashes = {
        name: _capture_file(manifest_path, name, files.get(name))
        for name in required_files
    }
    rows = {
        name: (
            _read_rows_v2(path, name, capture_id)
            if version_two else _read_rows(path, name, capture_id)
        )
        for name, (path, _digest) in paths_and_hashes.items()
        if name != "registry"
    }
    registry = (
        _read_registry_v2(paths_and_hashes["registry"][0], capture_id)
        if version_two else None
    )
    for name, values in rows.items():
        for row in values:
            timestamp = _timestamp(row["timestamp_utc"])
            if not capture_start <= timestamp <= capture_end:
                raise ValueError(f"{capture_id}/{name} timestamp lies outside acquisition interval")
    rf_drone_ids = {row["drone_id"] for row in rows["rf"]}
    if version_two:
        if {row["drone_id"] for row in rows["labels"]} != rf_drone_ids:
            raise ValueError("label identities do not match the RF fleet")
        for name in ("reported", "network"):
            if not {row["drone_id"] for row in rows[name]}.issubset(rf_drone_ids):
                raise ValueError(f"{name} contains identities outside the RF fleet")
        if not rf_drone_ids.issubset(registry or {}):
            raise ValueError("frozen registry does not bind all RF drones")
    else:
        if not rf_drone_ids.issubset({row["drone_id"] for row in rows["network"]}):
            raise ValueError("network evidence does not cover all RF drones")
        if not rf_drone_ids.issubset({row["drone_id"] for row in rows["labels"]}):
            raise ValueError("independent labels do not cover all RF drones")
    if version_two:
        network_lag, missing_network = _v2_alignment_profile(
            rows["rf"], rows["network"], capture_id, "network", tolerance_ms,
        )
    else:
        network_lag = _aligned_lag_ms(
            rows["rf"], rows["network"], capture_id, "network", tolerance_ms,
        )
        missing_network = set()
    label_lag = _aligned_lag_ms(
        rows["rf"], rows["labels"], capture_id, "labels", tolerance_ms,
    )
    report_lag = None
    usable_port_windows = None
    missing_report: set[tuple[str, datetime]] = set()
    unpaired_network = None
    if version_two:
        report_lag, missing_report = _v2_alignment_profile(
            rows["rf"], rows["reported"], capture_id, "reported", tolerance_ms,
        )
        _aligned_lag_ms(rows["labels"], rows["rf"], capture_id, "rf", tolerance_ms)
        usable_port_windows, unpaired_network = _check_v2_windows(
            rows["reported"], rows["network"], capture_id,
            tolerance_ms, rf_drone_ids,
        )
    fingerprint_payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": protocol,
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
        "protocol_version": protocol,
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
        "max_report_alignment_lag_ms": report_lag,
        "three_path_rf_network_inputs_structurally_present": version_two,
        "offline_model_replay_ready": False,
        "usable_port_windows_by_drone": usable_port_windows,
        "unavailable_evidence_timesteps": (
            {
                "controller_missing": len(missing_network),
                "participant_report_missing": len(missing_report),
                "joint_missing": len(missing_network | missing_report),
                "unpaired_controller_observations": unpaired_network,
                "controller_identity_missing": sum(
                    not row["observed_source_mac"].strip()
                    for row in rows["network"]
                ),
            }
            if version_two else None
        ),
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
    protocols = {capture["protocol_version"] for capture in captures}
    if len(protocols) != 1:
        raise ValueError("development and held-out captures must use one protocol version")
    ids = [capture["capture_id"] for capture in captures]
    if len(ids) != len(set(ids)):
        raise ValueError("capture_id is reused across the study")
    development = {capture["campaign_id"] for capture in captures if capture["role"] == "development"}
    held_out = {capture["campaign_id"] for capture in captures if capture["role"] == "held_out"}
    if not development or not held_out or development & held_out:
        raise ValueError("development and held-out captures require disjoint campaign IDs")
    checked_files = ("rf", "reported", "network", "labels") if PROTOCOL_V2 in protocols else ("rf", "network")
    for name in checked_files:
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
        "protocol_version": captures[0]["protocol_version"],
        "preflight_status": "structurally_ready_source_unverified",
        "validation_category": "structure_only",
        "source_review_required": True,
        "captures": captures,
    }
