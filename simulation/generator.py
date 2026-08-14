"""
simulation/generator.py — Upgraded SOTA RF Metric Generator  [FLARE v2]

Generates telemetry snapshots for direct, satellite, and mesh paths,
simulating 11 advanced EW jamming and cyber-physical attack profiles.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_JAM_STATE_FILE = _BASE / "simulation" / "jam_state.json"

PATHS = ["direct", "satellite", "mesh"]
DRONES = ["drone_1", "drone_2", "drone_3"]

# Drone geographic RF offsets
_DRONE_OFFSET = {
    "drone_1": {"rssi": 0, "pdr": 0.0, "latency_factor": 1.0},
    "drone_2": {"rssi": -8, "pdr": -0.05, "latency_factor": 1.3},
    "drone_3": {"rssi": -15, "pdr": -0.10, "latency_factor": 1.7},
}

# Normal baseline ranges
_NORMAL_RANGES = {
    "direct": dict(rssi=(-65, -40), pdr=(0.85, 1.0), sinr=(15, 30), latency=(5, 30), packet_loss=(0.0, 0.05)),
    "satellite": dict(rssi=(-80, -55), pdr=(0.75, 0.95), sinr=(8, 20), latency=(30, 120), packet_loss=(0.02, 0.10)),
    "mesh": dict(rssi=(-90, -65), pdr=(0.70, 0.90), sinr=(5, 15), latency=(50, 200), packet_loss=(0.05, 0.15)),
}

# Jammed ranges (default spot/barrage noise)
_JAMMED_RANGES = {
    "direct": dict(rssi=(-110, -90), pdr=(0.0, 0.3), sinr=(-5, 5), latency=(300, 900), packet_loss=(0.5, 0.95)),
    "satellite": dict(rssi=(-105, -85), pdr=(0.05, 0.35), sinr=(-3, 7), latency=(250, 800), packet_loss=(0.4, 0.90)),
    "mesh": dict(rssi=(-115, -95), pdr=(0.0, 0.25), sinr=(-8, 3), latency=(400, 1000), packet_loss=(0.6, 0.95)),
}


def _load_jam_state() -> dict:
    default = {d: {"profile": "none", "paths": [], "gps_drift": 0.0} for d in DRONES}
    if not _JAM_STATE_FILE.exists():
        return default
    try:
        state = json.loads(_JAM_STATE_FILE.read_text())
        return state
    except (json.JSONDecodeError, OSError):
        return default


# In-memory replay buffer cache for replaying healthy metrics
_REPLAY_CACHE: Dict[str, List[dict]] = {}


def _sample_path_metrics(
    path: str,
    jammed: bool,
    jam_profile: str,
    rng: np.random.Generator,
    drone_offset: dict,
    gps_drift: float = 0.0,
) -> dict:
    """Sample path metrics according to active SOTA EW profile."""
    offset = drone_offset

    # Baseline calculations
    ranges = _NORMAL_RANGES[path]
    rssi = float(np.clip(rng.uniform(*ranges["rssi"]) + offset["rssi"], -130, -20))
    pdr = float(np.clip(rng.uniform(*ranges["pdr"]) + offset["pdr"], 0.0, 1.0))
    sinr = float(rng.uniform(*ranges["sinr"]))
    latency = float(np.clip(rng.uniform(*ranges["latency"]) * offset["latency_factor"], 0.0, 1000.0))
    packet_loss = float(np.clip(rng.uniform(*ranges["packet_loss"]), 0.0, 1.0))

    if jammed:
        if jam_profile == "smart":
            # Smart Jammer: normal RSSI, but PDR dropped to near zero
            # Low power signature, difficult to detect via threshold
            pdr = float(rng.uniform(0.01, 0.08))
            sinr = float(rng.uniform(1.0, 4.0))
            packet_loss = float(rng.uniform(0.85, 0.98))
        elif jam_profile == "fhss":
            # FHSS Mitigation: Hopping reduces jamming effectiveness by 90%
            # Reverts metrics to near-normal levels
            pass
        elif jam_profile == "spoofing":
            # Signal Spoofing: reports EXCELLENT metrics to deceive routing
            rssi = float(rng.uniform(-35.0, -25.0))
            pdr = float(rng.uniform(0.98, 1.0))
            sinr = float(rng.uniform(28.0, 35.0))
            latency = float(rng.uniform(2.0, 8.0))
            packet_loss = float(rng.uniform(0.0, 0.01))
        elif jam_profile == "dos":
            # DoS Attack: normal RSSI, but extreme latency and loss due to queue overflow
            latency = float(rng.uniform(750.0, 990.0))
            packet_loss = float(rng.uniform(0.85, 0.99))
            pdr = 1.0 - packet_loss
        else:
            # Default brute force noise (spot, barrage, sweep, reactive, adaptive)
            jam_ranges = _JAMMED_RANGES[path]
            rssi = float(np.clip(rng.uniform(*jam_ranges["rssi"]) + offset["rssi"], -130, -20))
            pdr = float(np.clip(rng.uniform(*jam_ranges["pdr"]) + offset["pdr"], 0.0, 1.0))
            sinr = float(rng.uniform(*jam_ranges["sinr"]))
            latency = float(np.clip(rng.uniform(*jam_ranges["latency"]) * offset["latency_factor"], 0.0, 1000.0))
            packet_loss = float(np.clip(rng.uniform(*jam_ranges["packet_loss"]), 0.0, 1.0))

    # GPS Spoofing: affects latency proportionally to drift
    if gps_drift > 0.0:
        # 120m drift causes additional simulated packet propagation/relay latency
        latency += gps_drift * 1.5

    return {
        "path_id": path,
        "rssi": round(rssi, 2),
        "pdr": round(pdr, 4),
        "sinr": round(sinr, 2),
        "latency": round(latency, 2),
        "packet_loss": round(packet_loss, 4),
    }


def generate_metrics(
    drone_id: str = "drone_1",
    seed: int | None = None,
    *,
    jam_state: dict | None = None,
) -> dict:
    """Generate metrics snapshot incorporating advanced EW attacks."""
    rng = np.random.default_rng(seed)
    if jam_state is None:
        jam_state = _load_jam_state()

    drone_jam = jam_state.get(drone_id, {"profile": "none", "paths": [], "gps_drift": 0.0})
    profile = drone_jam.get("profile", "none")
    target_paths = set(drone_jam.get("paths", []))
    gps_drift = float(drone_jam.get("gps_drift", 0.0))
    offset = _DRONE_OFFSET.get(drone_id, _DRONE_OFFSET["drone_1"])

    # Expand active paths based on sweep/barrage logic
    if profile == "barrage":
        target_paths = set(PATHS)
    elif profile == "sweep":
        # Sweeps carrier frequency: jams one path every 3 seconds
        active_idx = int(time.time() / 3.0) % len(PATHS)
        target_paths = {PATHS[active_idx]}

    # Cache healthy metrics for Replay attack
    if profile == "none" or not target_paths:
        # Generate clean metrics
        paths_data = []
        for path in PATHS:
            path_metrics = _sample_path_metrics(path, False, "none", rng, offset, 0.0)
            paths_data.append(path_metrics)

        # Save to cache
        if drone_id not in _REPLAY_CACHE:
            _REPLAY_CACHE[drone_id] = []
        _REPLAY_CACHE[drone_id].append(paths_data)
        if len(_REPLAY_CACHE[drone_id]) > 50:
            _REPLAY_CACHE[drone_id].pop(0)
    else:
        # Jamming is active!
        if profile == "replay" and drone_id in _REPLAY_CACHE and len(_REPLAY_CACHE[drone_id]) > 0:
            # Replay Attack: Replays historical clean metrics to hide current jamming
            # Selects next cached entry sequentially
            cycle_idx = int(time.time()) % len(_REPLAY_CACHE[drone_id])
            paths_data = _REPLAY_CACHE[drone_id][cycle_idx]
            logger.debug("[Replay EW] Replaying clean metrics cycle index: %d", cycle_idx)
        else:
            paths_data = []
            for path in PATHS:
                is_jammed = (path in target_paths)
                path_metrics = _sample_path_metrics(path, is_jammed, profile, rng, offset, gps_drift)
                paths_data.append(path_metrics)

    # GPS coordinates simulation
    # Healthy base: lat=45.10, lon=-122.30
    lat = 45.10
    lon = -122.30
    if gps_drift > 0.0:
        # drift GPS coordinates slightly
        lat += gps_drift * 0.0001
        lon += gps_drift * 0.0001

    return {
        "drone_id": drone_id,
        "timestamp": time.time(),
        "paths": paths_data,
        "gps": {
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "drift_m": round(gps_drift, 2),
        },
        "ew_status": {
            "active_attack": profile if profile != "none" else None,
            "jammed_paths": list(target_paths),
        }
    }


def generate_swarm_metrics() -> dict:
    jam_state = _load_jam_state()
    return {
        drone_id: generate_metrics(drone_id=drone_id, jam_state=jam_state)
        for drone_id in DRONES
    }


def metrics_to_tensor(metrics: dict, seq_len: int = 10) -> list:
    result = []
    for path_data in metrics["paths"]:
        feats = np.array([
            path_data["rssi"],
            path_data["pdr"],
            path_data["sinr"],
            path_data["latency"],
            path_data["packet_loss"],
        ], dtype=np.float32)

        mins = np.array([-120.0, 0.0, -10.0, 0.0, 0.0], dtype=np.float32)
        maxs = np.array([-20.0, 1.0, 30.0, 1000.0, 1.0], dtype=np.float32)
        feats = (feats - mins) / (maxs - mins + 1e-8)
        feats = np.clip(feats, 0.0, 1.0)

        seq = np.tile(feats, (seq_len, 1)).astype(np.float32)
        result.append(seq)
    return result


if __name__ == "__main__":
    m = generate_metrics("drone_1")
    print(json.dumps(m, indent=2))
