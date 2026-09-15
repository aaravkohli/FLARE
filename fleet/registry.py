"""File-backed, process-safe-enough fleet registry used by every FLARE layer.

Enrollment remains an authenticated control-plane action. Reading is cheap and
mtime-cached so the orchestrator, Flower server, API, simulator, and SDN service
can discover newly enrolled drones without accepting arbitrary claimed IDs.
"""

from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Mapping

import yaml


_BASE = Path(__file__).resolve().parent.parent
_REGISTRY_PATH = Path(
    os.getenv("FLARE_FLEET_REGISTRY", str(_BASE / "config" / "fleet_registry.yaml"))
)
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_MAC_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
_LOCK = threading.RLock()
_CACHE_MTIME_NS: int | None = None
_CACHE: dict[str, dict[str, Any]] | None = None


class DroneRegistrationError(ValueError):
    """Raised when an enrollment would create an invalid or ambiguous identity."""


def registry_path() -> Path:
    return _REGISTRY_PATH


def _validate_record(drone_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    normalized_id = str(drone_id).strip().lower()
    if not _ID_PATTERN.fullmatch(normalized_id):
        raise DroneRegistrationError(
            "drone_id must be 3-64 lowercase letters, numbers, hyphens, or underscores"
        )
    mac = str(raw.get("mac", "")).strip().lower()
    if not _MAC_PATTERN.fullmatch(mac):
        raise DroneRegistrationError("mac must be a six-octet colon-separated address")
    try:
        access_port = int(raw["access_port"])
        index = int(raw["index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DroneRegistrationError("index and access_port must be integers") from exc
    if access_port < 4 or index < 1:
        raise DroneRegistrationError("index must be positive and access_port must be 4 or higher")

    rf = raw.get("rf_profile", {})
    if not isinstance(rf, Mapping):
        raise DroneRegistrationError("rf_profile must be an object")
    rssi_offset = float(rf.get("rssi_offset", 0.0))
    pdr_offset = float(rf.get("pdr_offset", 0.0))
    latency_factor = float(rf.get("latency_factor", 1.0))
    if not all(math.isfinite(value) for value in (rssi_offset, pdr_offset, latency_factor)):
        raise DroneRegistrationError("RF profile values must be finite numbers")
    if not -60.0 <= rssi_offset <= 60.0:
        raise DroneRegistrationError("rssi_offset must be between -60 and 60 dB")
    if not -1.0 <= pdr_offset <= 1.0:
        raise DroneRegistrationError("pdr_offset must be between -1 and 1")
    if not 0.1 <= latency_factor <= 10.0:
        raise DroneRegistrationError("latency_factor must be between 0.1 and 10")

    display_name = str(
        raw.get("display_name") or normalized_id.replace("_", " ").title()
    ).strip()
    if not display_name or len(display_name) > 80:
        raise DroneRegistrationError("display_name must contain 1-80 characters")

    return {
        "drone_id": normalized_id,
        "display_name": display_name,
        "index": index,
        "mac": mac,
        "access_port": access_port,
        "enabled": bool(raw.get("enabled", True)),
        "rf_profile": {
            "rssi_offset": rssi_offset,
            "pdr_offset": pdr_offset,
            "latency_factor": latency_factor,
        },
    }


def _read_uncached() -> dict[str, dict[str, Any]]:
    if not _REGISTRY_PATH.exists():
        raise RuntimeError(f"Fleet registry is missing: {_REGISTRY_PATH}")
    raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    entries = raw.get("drones", {})
    if not isinstance(entries, Mapping) or not entries:
        raise RuntimeError("Fleet registry must contain at least one drone")
    validated = {
        str(drone_id): _validate_record(str(drone_id), values)
        for drone_id, values in entries.items()
        if isinstance(values, Mapping)
    }
    macs = [record["mac"] for record in validated.values()]
    ports = [record["access_port"] for record in validated.values()]
    indexes = [record["index"] for record in validated.values()]
    if len(macs) != len(set(macs)):
        raise RuntimeError("Fleet registry contains duplicate MAC addresses")
    if len(ports) != len(set(ports)):
        raise RuntimeError("Fleet registry contains duplicate access ports")
    if len(indexes) != len(set(indexes)):
        raise RuntimeError("Fleet registry contains duplicate indexes")
    return validated


def list_drones(*, enabled_only: bool = True, force_reload: bool = False) -> list[dict[str, Any]]:
    global _CACHE, _CACHE_MTIME_NS
    with _LOCK:
        mtime_ns = _REGISTRY_PATH.stat().st_mtime_ns if _REGISTRY_PATH.exists() else None
        if force_reload or _CACHE is None or mtime_ns != _CACHE_MTIME_NS:
            _CACHE = _read_uncached()
            _CACHE_MTIME_NS = mtime_ns
        records = sorted(_CACHE.values(), key=lambda record: (record["index"], record["drone_id"]))
        if enabled_only:
            records = [record for record in records if record["enabled"]]
        return deepcopy(records)


def active_drone_ids() -> tuple[str, ...]:
    return tuple(record["drone_id"] for record in list_drones(enabled_only=True))


def is_active_drone(drone_id: str) -> bool:
    return str(drone_id) in set(active_drone_ids())


def get_drone(drone_id: str, *, enabled_only: bool = True) -> dict[str, Any] | None:
    normalized = str(drone_id).strip().lower()
    for record in list_drones(enabled_only=enabled_only):
        if record["drone_id"] == normalized:
            return record
    return None


def _persist_records(records: Mapping[str, Mapping[str, Any]]) -> None:
    """Atomically replace the registry while keeping disabled identities reserved."""
    global _CACHE, _CACHE_MTIME_NS
    payload = {
        "version": 1,
        "drones": {
            key: {field: value for field, value in value.items() if field != "drone_id"}
            for key, value in sorted(records.items(), key=lambda item: item[1]["index"])
        },
    }
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{_REGISTRY_PATH.name}.", suffix=".tmp", dir=_REGISTRY_PATH.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, _REGISTRY_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    _CACHE = None
    _CACHE_MTIME_NS = None


def update_drone(
    drone_id: str,
    *,
    display_name: str | None = None,
    rssi_offset: float | None = None,
    pdr_offset: float | None = None,
    latency_factor: float | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Edit safe registry fields; identity and SDN topology bindings are immutable."""
    with _LOCK:
        records = {
            record["drone_id"]: record
            for record in list_drones(enabled_only=False, force_reload=True)
        }
        normalized_id = str(drone_id).strip().lower()
        if normalized_id not in records:
            raise DroneRegistrationError(f"drone_id {normalized_id!r} is not registered")
        record = deepcopy(records[normalized_id])
        if display_name is not None:
            record["display_name"] = display_name
        rf = record["rf_profile"]
        if rssi_offset is not None:
            rf["rssi_offset"] = rssi_offset
        if pdr_offset is not None:
            rf["pdr_offset"] = pdr_offset
        if latency_factor is not None:
            rf["latency_factor"] = latency_factor
        if enabled is not None:
            record["enabled"] = enabled
        record = _validate_record(normalized_id, record)
        if not record["enabled"] and sum(
            candidate["enabled"] if key != normalized_id else False
            for key, candidate in records.items()
        ) < 2:
            raise DroneRegistrationError("at least two active drones are required for Flower rounds")
        records[normalized_id] = record
        _persist_records(records)
        return deepcopy(record)


def register_drone(
    *,
    drone_id: str,
    mac: str,
    access_port: int,
    display_name: str | None = None,
    rssi_offset: float = 0.0,
    pdr_offset: float = 0.0,
    latency_factor: float = 1.0,
) -> dict[str, Any]:
    """Persist one new enabled drone after enforcing identity/topology uniqueness."""
    global _CACHE, _CACHE_MTIME_NS
    with _LOCK:
        records = {record["drone_id"]: record for record in list_drones(enabled_only=False, force_reload=True)}
        normalized_id = str(drone_id).strip().lower()
        if normalized_id in records:
            raise DroneRegistrationError(f"drone_id {normalized_id!r} is already registered")
        normalized_mac = str(mac).strip().lower()
        if any(record["mac"] == normalized_mac for record in records.values()):
            raise DroneRegistrationError(f"MAC address {normalized_mac!r} is already registered")
        if any(record["access_port"] == int(access_port) for record in records.values()):
            raise DroneRegistrationError(f"access_port {access_port} is already registered")
        next_index = max(record["index"] for record in records.values()) + 1
        record = _validate_record(normalized_id, {
            "display_name": display_name,
            "index": next_index,
            "mac": normalized_mac,
            "access_port": access_port,
            "enabled": True,
            "rf_profile": {
                "rssi_offset": rssi_offset,
                "pdr_offset": pdr_offset,
                "latency_factor": latency_factor,
            },
        })
        records[normalized_id] = record
        _persist_records(records)
        return deepcopy(record)
