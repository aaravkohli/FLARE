"""Shared, simulation-only control state for federated client update attacks."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Dict

VALID_ATTACK_MODES = {"normal", "noisy", "poisoned"}
_BASE = Path(__file__).parent.parent
DEFAULT_STATE_PATH = _BASE / "simulation" / "byzantine_state.json"


def _resolve_path(path: Path | None) -> Path:
    if path is not None:
        return path
    configured = os.getenv("FLARE_BYZANTINE_STATE_PATH")
    return Path(configured) if configured else DEFAULT_STATE_PATH


def load_attack_state(path: Path | None = None) -> Dict[str, str]:
    path = _resolve_path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(client_id): str(mode)
        for client_id, mode in raw.items()
        if mode in VALID_ATTACK_MODES
    }


def get_attack_mode(client_id: str, default: str = "normal", path: Path | None = None) -> str:
    if default not in VALID_ATTACK_MODES:
        raise ValueError(f"Unknown default attack mode: {default}")
    return load_attack_state(path).get(client_id, default)


def _write_attack_state(state: Dict[str, str], path: Path) -> None:
    path = _resolve_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def set_attack_mode(client_id: str, mode: str, path: Path | None = None) -> None:
    if mode not in VALID_ATTACK_MODES:
        raise ValueError(f"Unknown attack mode: {mode}")
    resolved_path = _resolve_path(path)
    state = load_attack_state(resolved_path)
    if mode == "normal":
        state.pop(client_id, None)
    else:
        state[client_id] = mode
    _write_attack_state(state, resolved_path)


def reset_attack_state(path: Path | None = None) -> None:
    """Atomically restore every simulated FL client to normal behavior."""
    _write_attack_state({}, _resolve_path(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="clear every persisted simulated Byzantine client mode",
    )
    args = parser.parse_args()
    if not args.reset:
        parser.error("no action selected; use --reset")
    reset_attack_state()
    print("Federated client simulation modes reset to normal.")


if __name__ == "__main__":
    main()
