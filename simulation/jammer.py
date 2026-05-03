"""
simulation/jammer.py — [SIMULATED]
CLI tool to simulate RF jamming on selected communication paths.

Writes jam_state.json which is read by generator.py to inject
degraded RF metrics for the duration of the jamming event.

Usage:
  python simulation/jammer.py --jam direct --duration 10
  python simulation/jammer.py --jam direct satellite --duration 5
  python simulation/jammer.py --clear
"""

import argparse
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [JAMMER] %(message)s")
logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_JAM_STATE_FILE = _BASE / "simulation" / "jam_state.json"
VALID_PATHS = {"direct", "satellite", "mesh"}


def _load_state() -> dict:
    if not _JAM_STATE_FILE.exists():
        return {d: {p: False for p in VALID_PATHS} for d in ["drone_1", "drone_2", "drone_3"]}
    try:
        state = json.loads(_JAM_STATE_FILE.read_text())
        if "direct" in state and isinstance(state["direct"], bool):
            return {d: state for d in ["drone_1", "drone_2", "drone_3"]}
        return state
    except (json.JSONDecodeError, OSError):
        return {d: {p: False for p in VALID_PATHS} for d in ["drone_1", "drone_2", "drone_3"]}


def _write_state(state: dict) -> None:
    _JAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _JAM_STATE_FILE.write_text(json.dumps(state, indent=2))


def jam_paths(drone_id: str, paths: list[str], duration_s: float) -> None:
    """Enable jamming on the specified paths for duration_s seconds for a specific drone."""
    invalid = set(paths) - VALID_PATHS
    if invalid:
        raise ValueError(f"Invalid path(s): {invalid}. Must be one of {VALID_PATHS}")

    state = _load_state()
    if drone_id not in state:
        state[drone_id] = {p: False for p in VALID_PATHS}
        
    for p in VALID_PATHS:
        state[drone_id][p] = (p in paths)
        
    _write_state(state)
    logger.info("Jamming ACTIVE on %s for %s | Duration: %.1fs", paths, drone_id, duration_s)

    t0 = time.time()
    try:
        while time.time() - t0 < duration_s:
            elapsed = time.time() - t0
            remaining = duration_s - elapsed
            logger.info("  [%s] Jamming... %.1fs / %.1fs (remaining: %.1fs)", drone_id, elapsed, duration_s, remaining)
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        clear_jamming(drone_id)


def clear_jamming(drone_id: str = None) -> None:
    """Remove jamming for a specific drone, or all drones if None."""
    from simulation.generator import DRONES
    state = _load_state()
    
    if drone_id and drone_id != "all":
        state[drone_id] = {p: False for p in VALID_PATHS}
        logger.info("Jamming CLEARED for %s.", drone_id)
    else:
        # Clear ALL drones in the known swarm
        for d in DRONES:
            state[d] = {p: False for p in VALID_PATHS}
        logger.info("Jamming CLEARED — all paths restored for all %d drones.", len(DRONES))
        
    _write_state(state)


def main():
    parser = argparse.ArgumentParser(
        description="RF Jammer Simulator — writes jam_state.json consumed by generator.py"
    )
    parser.add_argument(
        "--jam", nargs="+", choices=list(VALID_PATHS),
        help="Paths to jam (e.g. --jam direct satellite)"
    )
    parser.add_argument(
        "--duration", type=float, default=10.0,
        help="Duration in seconds to jam (default: 10)"
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Clear all jamming immediately"
    )
    parser.add_argument(
        "--drone", type=str, default="drone_1",
        help="Drone to target (default: drone_1)"
    )
    args = parser.parse_args()

    if args.clear:
        clear_jamming(args.drone if args.drone != "all" else None)
    elif args.jam:
        jam_paths(args.drone, args.jam, args.duration)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
