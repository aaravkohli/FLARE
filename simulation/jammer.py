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
    default = {d: {"profile": "none", "paths": []} for d in ["drone_1", "drone_2", "drone_3"]}
    if not _JAM_STATE_FILE.exists():
        return default
    try:
        state = json.loads(_JAM_STATE_FILE.read_text())
        # Compatibility handling
        if isinstance(state, dict):
            # Old style format
            if "direct" in state and isinstance(state["direct"], bool):
                jammed_paths = [p for p in VALID_PATHS if state[p]]
                return {d: {"profile": "spot" if jammed_paths else "none", "paths": jammed_paths} for d in ["drone_1", "drone_2", "drone_3"]}
            # Old nested format
            first_val = next(iter(state.values()))
            if isinstance(first_val, dict) and "direct" in first_val and isinstance(first_val["direct"], bool):
                new_state = {}
                for d, paths_bool in state.items():
                    jammed_paths = [p for p in VALID_PATHS if paths_bool.get(p, False)]
                    new_state[d] = {"profile": "spot" if jammed_paths else "none", "paths": jammed_paths}
                return new_state
        return state
    except (json.JSONDecodeError, OSError):
        return default


def _write_state(state: dict) -> None:
    _JAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _JAM_STATE_FILE.write_text(json.dumps(state, indent=2))


def jam_paths(drone_id: str, paths: list[str], duration_s: float, profile: str = "spot") -> None:
    """Enable jamming on the specified paths using the given EW profile for duration_s seconds for a specific drone."""
    invalid = set(paths) - VALID_PATHS
    if invalid:
        raise ValueError(f"Invalid path(s): {invalid}. Must be one of {VALID_PATHS}")

    state = _load_state()
    if drone_id == "all":
        from simulation.generator import DRONES
        for d in DRONES:
            state[d] = {"profile": profile, "paths": paths}
    else:
        if drone_id not in state:
            state[drone_id] = {"profile": "none", "paths": []}
        state[drone_id] = {"profile": profile, "paths": paths}
        
    _write_state(state)
    logger.info("Jamming ACTIVE using profile '%s' on %s for %s | Duration: %.1fs", profile, paths, drone_id, duration_s)

    t0 = time.time()
    try:
        while time.time() - t0 < duration_s:
            elapsed = time.time() - t0
            remaining = duration_s - elapsed
            logger.info("  [%s] Jamming (%s)... %.1fs / %.1fs", drone_id, profile, elapsed, duration_s)
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
        state[drone_id] = {"profile": "none", "paths": []}
        logger.info("Jamming CLEARED for %s.", drone_id)
    else:
        for d in DRONES:
            state[d] = {"profile": "none", "paths": []}
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
