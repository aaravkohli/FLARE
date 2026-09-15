"""Controlled insider-traffic scenarios used only in simulation/testing."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Dict

import numpy as np


VALID_INSIDER_PROFILES = {
    "normal",
    "selective_forwarding",
    "telemetry_falsification",
    "control_flood",
    "replay",
}
_BASE = Path(__file__).parent.parent
DEFAULT_STATE_PATH = _BASE / "simulation" / "insider_state.json"


def load_insider_state(path: Path | None = None) -> Dict[str, str]:
    resolved = path or Path(
        os.getenv("FLARE_INSIDER_STATE_PATH", str(DEFAULT_STATE_PATH))
    )
    if not resolved.exists():
        return {}
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(client_id): str(profile)
        for client_id, profile in raw.items()
        if profile in VALID_INSIDER_PROFILES
    } if isinstance(raw, dict) else {}


def set_insider_profile(
    client_id: str, profile: str, path: Path | None = None
) -> None:
    if profile not in VALID_INSIDER_PROFILES:
        raise ValueError(f"unknown insider profile: {profile!r}")
    resolved = path or DEFAULT_STATE_PATH
    state = load_insider_state(resolved)
    if profile == "normal":
        state.pop(client_id, None)
    else:
        state[client_id] = profile
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=resolved.parent,
            prefix=f".{resolved.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, resolved)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def generate_insider_evidence(
    drone_id: str,
    *,
    seed: int | None = None,
    timestamp: float | None = None,
    profile_override: str | None = None,
) -> dict:
    profile = profile_override or load_insider_state().get(drone_id, "normal")
    if profile not in VALID_INSIDER_PROFILES:
        raise ValueError(f"unknown insider profile: {profile!r}")
    rng = np.random.default_rng(seed)
    now = float(timestamp if timestamp is not None else time.time())
    received = int(rng.integers(90, 121))
    forwarded = max(0, received - int(rng.integers(0, 4)))
    reported = forwarded
    control_rate = float(rng.uniform(1.0, 6.0))
    duplicate_ratio = float(rng.uniform(0.0, 0.03))
    if profile == "selective_forwarding":
        forwarded = int(received * rng.uniform(0.10, 0.35))
        reported = received
    elif profile == "telemetry_falsification":
        forwarded = int(received * rng.uniform(0.45, 0.65))
        reported = received
    elif profile == "control_flood":
        control_rate = float(rng.uniform(80.0, 140.0))
        duplicate_ratio = float(rng.uniform(0.25, 0.45))
    elif profile == "replay":
        duplicate_ratio = float(rng.uniform(0.70, 0.98))
        reported = received + int(rng.integers(20, 50))
    return {
        "reported_tx_packets": received,
        "controller_rx_packets": received,
        "controller_forwarded_packets": forwarded,
        "reported_forwarded_packets": reported,
        "control_messages_per_s": control_rate,
        "duplicate_sequence_ratio": duplicate_ratio,
        "timestamp": now,
        "controller_timestamp": now + float(rng.uniform(0.0, 0.05)),
        "simulation_profile": profile,
    }
