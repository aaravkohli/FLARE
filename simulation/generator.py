"""
simulation/generator.py — [SIMULATED]
Synthetic RF metric generator for all 3 communication paths.

Reads jam_state.json written by jammer.py to inject degraded metrics
for jammed paths. Generates metrics conforming to schemas/metrics.json.

Usage (programmatic):
    from simulation.generator import generate_metrics
    metrics = generate_metrics(drone_id="drone_1")
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Dict

import numpy as np

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_JAM_STATE_FILE = _BASE / "simulation" / "jam_state.json"

PATHS = ["direct", "satellite", "mesh"]
DRONES = ["drone_1", "drone_2", "drone_3"]

# Per-drone RF offset (simulates different geographic position / altitude)
# Positive = better signal; negative = worse
_DRONE_OFFSET = {
    "drone_1": { "rssi": 0,   "pdr": 0.0,   "latency_factor": 1.0 },
    "drone_2": { "rssi": -8,  "pdr": -0.05, "latency_factor": 1.3 },  # farther
    "drone_3": { "rssi": -15, "pdr": -0.10, "latency_factor": 1.7 },  # mesh relay
}

# Normal RF metric ranges per path
_NORMAL_RANGES = {
    "direct":    dict(rssi=(-65, -40),  pdr=(0.85, 1.0),  sinr=(15, 30),  latency=(5, 30),    packet_loss=(0.0, 0.05)),
    "satellite": dict(rssi=(-80, -55),  pdr=(0.75, 0.95), sinr=(8, 20),   latency=(30, 120),  packet_loss=(0.02, 0.10)),
    "mesh":      dict(rssi=(-90, -65),  pdr=(0.70, 0.90), sinr=(5, 15),   latency=(50, 200),  packet_loss=(0.05, 0.15)),
}

# Degraded RF metric ranges under jamming
_JAMMED_RANGES = {
    "direct":    dict(rssi=(-110, -90), pdr=(0.0, 0.3),   sinr=(-5, 5),   latency=(300, 900), packet_loss=(0.5, 0.95)),
    "satellite": dict(rssi=(-105, -85), pdr=(0.05, 0.35), sinr=(-3, 7),   latency=(250, 800), packet_loss=(0.4, 0.90)),
    "mesh":      dict(rssi=(-115, -95), pdr=(0.0, 0.25),  sinr=(-8, 3),   latency=(400,1000), packet_loss=(0.6, 0.95)),
}


def _load_jam_state() -> Dict[str, Dict[str, bool]]:
    """Load current jamming state from jam_state.json. Returns {drone_id: {path: is_jammed}}."""
    default = {d: {p: False for p in PATHS} for d in DRONES}
    if not _JAM_STATE_FILE.exists():
        return default
    try:
        state = json.loads(_JAM_STATE_FILE.read_text())
        # Ensure schema compatibility with old files
        if "direct" in state and isinstance(state["direct"], bool):
            return {d: state for d in DRONES}
        return state
    except (json.JSONDecodeError, OSError):
        return default


def _sample_path_metrics(path: str, jammed: bool, rng: np.random.Generator,
                         drone_offset: dict | None = None) -> dict:
    """Sample one set of RF metrics for a path, normal or jammed, with per-drone offset."""
    ranges = _JAMMED_RANGES[path] if jammed else _NORMAL_RANGES[path]
    offset = drone_offset or {"rssi": 0, "pdr": 0.0, "latency_factor": 1.0}

    rssi        = float(np.clip(rng.uniform(*ranges["rssi"]) + offset["rssi"], -130, -20))
    pdr         = float(np.clip(rng.uniform(*ranges["pdr"]) + offset["pdr"], 0.0, 1.0))
    sinr        = float(rng.uniform(*ranges["sinr"]))
    latency     = float(rng.uniform(*ranges["latency"]) * offset["latency_factor"])
    packet_loss = float(np.clip(rng.uniform(*ranges["packet_loss"]), 0.0, 1.0))

    return {
        "path_id":     path,
        "rssi":        round(rssi, 2),
        "pdr":         round(pdr, 4),
        "sinr":        round(sinr, 2),
        "latency":     round(latency, 2),
        "packet_loss": round(packet_loss, 4),
    }


def generate_metrics(drone_id: str = "drone_1", seed: int | None = None) -> dict:
    """
    Generate a single metrics snapshot for the given drone.
    Conforms to schemas/metrics.json. Applies per-drone RF offset.
    """
    rng = np.random.default_rng(seed)
    jam_state = _load_jam_state()
    # Default to unjammed if drone_id is unknown
    drone_jam = jam_state.get(drone_id, {p: False for p in PATHS})
    offset = _DRONE_OFFSET.get(drone_id, _DRONE_OFFSET["drone_1"])

    paths_data = []
    for path in PATHS:
        is_jammed = drone_jam.get(path, False)
        path_metrics = _sample_path_metrics(path, is_jammed, rng, offset)
        paths_data.append(path_metrics)

    return {"drone_id": drone_id, "timestamp": time.time(), "paths": paths_data}


def generate_swarm_metrics() -> dict:
    """Generate a full swarm snapshot for all 3 drones simultaneously."""
    return {
        drone_id: generate_metrics(drone_id=drone_id)
        for drone_id in DRONES
    }


def metrics_to_tensor(metrics: dict, seq_len: int = 10) -> list:
    """
    Convert a metrics snapshot into a list of per-path feature vectors
    suitable for feeding into the FL model.

    Returns: List of 3 sequences, each [seq_len, 5] (one per path).
    Note: For simplicity, repeats the snapshot across the sequence window.
    In a real system, a rolling buffer would be used.
    """
    import numpy as np
    result = []
    for path_data in metrics["paths"]:
        feats = np.array([
            path_data["rssi"],
            path_data["pdr"],
            path_data["sinr"],
            path_data["latency"],
            path_data["packet_loss"],
        ], dtype=np.float32)

        # Normalise to [0, 1] — explicit float32 to avoid silent float64 upcast
        mins  = np.array([-120.0, 0.0, -10.0,  0.0,   0.0], dtype=np.float32)
        maxs  = np.array([ -40.0, 1.0,  30.0, 1000.0, 1.0], dtype=np.float32)
        feats = (feats - mins) / (maxs - mins + 1e-8)
        feats = np.clip(feats, 0.0, 1.0)

        # Repeat across sequence window (sliding window buffer in real system)
        seq = np.tile(feats, (seq_len, 1)).astype(np.float32)   # [seq_len, 5]
        result.append(seq)
    return result


if __name__ == "__main__":
    # Quick smoke test
    import json
    m = generate_metrics("drone_1")
    print(json.dumps(m, indent=2))
