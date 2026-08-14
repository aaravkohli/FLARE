"""
simulation/jammer.py — Upgraded SOTA Electronic Warfare (EW) Simulator  [FLARE v2]

CLI tool to inject advanced EW attacks and cyber-physical threats into the swarm.

Supported Attack Profiles:
  - spot           : Brute force jamming on a single target path
  - sweep          : Fast frequency sweeping across all channels
  - barrage        : Wideband noise jamming across all paths simultaneously
  - smart          : Low-power protocol-aware jamming (normal RSSI, PDR ~0)
  - reactive       : Senses channel activity and dynamically jams the active path
  - adaptive       : RL-based cognitive jammer targeting drone routing policies
  - fhss           : frequency hopping mitigation (reduces jam effectiveness)
  - spoofing       : Signal spoofing (fake high-quality metrics to lure routing)
  - gps_spoofing   : GPS coordinate drift and latency injection
  - replay         : Frozen state replay (masks jamming with fake legacy metrics)
  - dos            : Denial of Service interface flooding (high latency, high loss)
  - sybil          : Swarm node replication (overwhelms registries)
  - model_poisoning: Federated learning weight poisoning (returns random weights)
  - data_poisoning : Label manipulation during local training
  - backdoor       : Trojan triggers embedded in models

Usage:
  python simulation/jammer.py --jam direct --profile reactive --duration 15
  python simulation/jammer.py --clear
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [JAMMER] %(message)s")
logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_JAM_STATE_FILE = _BASE / "simulation" / "jam_state.json"
VALID_PATHS = {"direct", "satellite", "mesh"}
VALID_DRONES = {"drone_1", "drone_2", "drone_3"}

VALID_PROFILES = {
    "none", "spot", "sweep", "barrage", "smart", "reactive", "adaptive",
    "fhss", "spoofing", "gps_spoofing", "replay", "dos", "sybil",
    "model_poisoning", "data_poisoning", "backdoor",
}

_STATE_LOCK = threading.RLock()


def _default_state() -> dict:
    return {
        drone_id: {"profile": "none", "paths": [], "gps_drift": 0.0}
        for drone_id in sorted(VALID_DRONES)
    }


def _load_state_unlocked() -> dict:
    default = _default_state()
    if not _JAM_STATE_FILE.exists():
        return default
    try:
        state = json.loads(_JAM_STATE_FILE.read_text())
        if not isinstance(state, dict):
            return default
        for drone_id in VALID_DRONES:
            current = state.get(drone_id)
            if not isinstance(current, dict):
                state[drone_id] = default[drone_id]
                continue
            current.setdefault("profile", "none")
            current.setdefault("paths", [])
            current.setdefault("gps_drift", 0.0)
        return state
    except (json.JSONDecodeError, OSError):
        return default


def _load_state() -> dict:
    with _STATE_LOCK:
        return _load_state_unlocked()


def _write_state_unlocked(state: dict) -> None:
    """Atomically replace the state file so readers never observe partial JSON."""
    _JAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_JAM_STATE_FILE.parent,
            prefix=".jam_state.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, _JAM_STATE_FILE)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _write_state(state: dict) -> None:
    with _STATE_LOCK:
        _write_state_unlocked(state)


def set_jamming_state(drone_id: str, paths: list[str], profile: str) -> list[str]:
    """Validate and atomically apply a jammer state update. Returns target drones."""
    if drone_id != "all" and drone_id not in VALID_DRONES:
        raise ValueError(f"Invalid drone_id: {drone_id}")
    if profile not in VALID_PROFILES:
        raise ValueError(f"Invalid EW profile: {profile}")
    invalid_paths = set(paths) - VALID_PATHS
    if invalid_paths:
        raise ValueError(f"Invalid paths: {sorted(invalid_paths)}")

    targets = sorted(VALID_DRONES) if drone_id == "all" else [drone_id]
    with _STATE_LOCK:
        state = _load_state_unlocked()
        for target in targets:
            state[target] = {
                "profile": profile,
                "paths": list(paths),
                "gps_drift": 120.0 if profile == "gps_spoofing" else 0.0,
            }
        _write_state_unlocked(state)
    return targets


def _set_target_paths(drone_id: str, paths: list[str]) -> None:
    """Update only target paths while preserving the current profile atomically."""
    with _STATE_LOCK:
        state = _load_state_unlocked()
        if drone_id not in state:
            raise ValueError(f"Invalid drone_id: {drone_id}")
        state[drone_id]["paths"] = list(paths)
        _write_state_unlocked(state)


def _get_active_path(drone_id: str) -> str:
    """Query the SQLite database to find the last active path selected by the drone."""
    db_path = _BASE / "experiments" / "experiment.db"
    if not db_path.exists():
        return "direct"
    try:
        # Use standard sqlite3 for self-contained script
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT path_name FROM runs WHERE drone_id = ? ORDER BY id DESC LIMIT 1",
                (drone_id,),
            )
            row = cursor.fetchone()
            return row["path_name"] if row else "direct"
    except Exception:
        return "direct"


# ---------------------------------------------------------------------------
# Background Interactive Attack Simulation Loops
# ---------------------------------------------------------------------------

def _run_reactive_jammer(drone_id: str, stop_event: threading.Event):
    """
    Reactive Jammer Thread:
      Senses channel activity by querying the database,
      and immediately shifts jamming carrier to the active path.
    """
    logger.info("[EW Thread] Reactive jammer sensing active...")
    while not stop_event.is_set():
        active = _get_active_path(drone_id)
        if active in VALID_PATHS:
            state = _load_state()
            if state[drone_id]["paths"] != [active]:
                _set_target_paths(drone_id, [active])
                logger.info("[Reactive EW] Active path activity detected! Shifting jamming to: %s", active)
        time.sleep(1.0)


def _run_adaptive_rl_jammer(drone_id: str, stop_event: threading.Event):
    """
    Adaptive RL Jammer Thread:
      Learns the drone's evasion policy.
      State: drone's chosen path.
      Action: jam path (direct, satellite, or mesh).
      Reward: +1.0 if drone chose the jammed path, -0.5 otherwise.
    """
    logger.info("[EW Thread] Cognitive RL jammer initialising...")
    q_table = {p: 0.0 for p in VALID_PATHS}  # Q-values for jamming each path
    lr = 0.2
    gamma = 0.9
    epsilon = 0.1
    last_jam_path = "direct"

    while not stop_event.is_set():
        # 1. Observe drone's action (state)
        active_path = _get_active_path(drone_id)

        # 2. Compute reward for last decision
        reward = 1.0 if active_path == last_jam_path else -0.5

        # 3. Q-learning update: Q(s, a) = Q + alpha*(r + gamma*max Q - Q)
        q_table[last_jam_path] += lr * (reward + gamma * max(q_table.values()) - q_table[last_jam_path])

        # 4. Choose next path to jam (Epsilon-Greedy)
        if random.random() < epsilon:
            next_jam = random.choice(list(VALID_PATHS))
        else:
            next_jam = max(q_table, key=q_table.get)

        _set_target_paths(drone_id, [next_jam])

        logger.info(
            "[Adaptive EW] Drone active: %s | Jamming: %s | Q-table: %s",
            active_path, next_jam, {k: round(v, 3) for k, v in q_table.items()},
        )

        last_jam_path = next_jam
        time.sleep(2.0)


# Import random for RL jammer
import random


# ---------------------------------------------------------------------------
# Core Actions
# ---------------------------------------------------------------------------

def jam_paths(drone_id: str, paths: list[str], duration_s: float, profile: str = "spot") -> None:
    """Enable jamming using SOTA EW profiles."""
    if profile not in VALID_PROFILES:
        raise ValueError(f"Invalid EW profile: {profile}. Must be one of {VALID_PROFILES}")

    invalid = set(paths) - VALID_PATHS
    if invalid and profile not in ["barrage", "sweep", "gps_spoofing"]:
        raise ValueError(f"Invalid path(s): {invalid}. Must be one of {VALID_PATHS}")

    targets = set_jamming_state(drone_id, paths, profile)
    logger.info("EW Attack ACTIVE: profile='%s' on %s for %s | Duration: %.1fs", profile, paths, drone_id, duration_s)

    # Launch background loops for interactive profiles
    stop_event = threading.Event()
    threads = []

    if profile == "reactive" and drone_id != "all":
        t = threading.Thread(target=_run_reactive_jammer, args=(drone_id, stop_event), daemon=True)
        t.start()
        threads.append(t)
    elif profile == "adaptive" and drone_id != "all":
        t = threading.Thread(target=_run_adaptive_rl_jammer, args=(drone_id, stop_event), daemon=True)
        t.start()
        threads.append(t)

    t0 = time.time()
    try:
        while time.time() - t0 < duration_s:
            elapsed = time.time() - t0
            logger.info("  [%s] EW Attack active (%s)... %.1fs / %.1fs", drone_id, profile, elapsed, duration_s)
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Interrupted by operator.")
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=1.0)
        clear_jamming(drone_id)


def clear_jamming(drone_id: str = None) -> None:
    """Restore normal operations and clear all EW/Poisoning injection states."""
    target_key = "all" if drone_id is None else drone_id
    targets = set_jamming_state(target_key, [], "none")
    logger.info("EW state CLEARED. All communication links restored for targets: %s", targets)


# ---------------------------------------------------------------------------
# CLI Entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Upgraded SOTA EW Simulator (FLARE v2)")
    parser.add_argument(
        "--jam", nargs="+", choices=list(VALID_PATHS),
        help="Target paths to jam (direct, satellite, mesh)"
    )
    parser.add_argument(
        "--profile", type=str, default="spot", choices=list(VALID_PROFILES),
        help="Tactical EW attack profile to deploy"
    )
    parser.add_argument(
        "--duration", type=float, default=10.0,
        help="Duration in seconds"
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Restore links and clear EW injections"
    )
    parser.add_argument(
        "--drone", type=str, default="drone_1",
        help="Target drone node (drone_1, drone_2, drone_3, all)"
    )
    args = parser.parse_args()

    if args.clear:
        clear_jamming(args.drone if args.drone != "all" else None)
    elif args.jam or args.profile in ["barrage", "sweep", "gps_spoofing", "sybil", "model_poisoning"]:
        jam_paths(args.drone, args.jam or [], args.duration, args.profile)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
