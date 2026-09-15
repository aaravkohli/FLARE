"""Shared route identifiers used by the orchestrator and SDN services."""

from __future__ import annotations

from fleet.registry import active_drone_ids, is_active_drone

ROUTABLE_PATHS = ("direct", "satellite", "mesh")
HOLD_PATH = "hold"
ROUTE_COMMANDS = (*ROUTABLE_PATHS, HOLD_PATH)
FALLBACK_PATH = "fallback"
VALID_PATHS = frozenset((*ROUTABLE_PATHS, FALLBACK_PATH))
# Compatibility snapshot for older imports. Runtime request validation must use
# ``is_registered_drone`` so newly enrolled drones are accepted without restart.
VALID_DRONES = frozenset(active_drone_ids())


def registered_drone_ids() -> frozenset[str]:
    return frozenset(active_drone_ids())


def is_registered_drone(drone_id: str) -> bool:
    return is_active_drone(drone_id)

PATH_TO_ACTION = {
    "direct": 0,
    "satellite": 1,
    "mesh": 2,
    # The controller's emergency fallback uses the physical mesh port.
    "fallback": 2,
    "hold": 3,
}


def normalize_installed_path(path_name: str) -> str:
    """Translate the controller-only fallback label to its physical route."""
    if path_name == HOLD_PATH:
        return HOLD_PATH
    if path_name not in VALID_PATHS:
        raise ValueError(f"unknown SDN path: {path_name!r}")
    return "mesh" if path_name == FALLBACK_PATH else path_name


def action_id_for_path(path_name: str) -> int:
    """Return the RL action represented by an SDN path name."""
    try:
        return PATH_TO_ACTION[path_name]
    except KeyError as exc:
        raise ValueError(f"unknown SDN path: {path_name!r}") from exc


def validate_route_action(path_name: str, action_id: int) -> None:
    """Reject ambiguous commands where the route and action disagree."""
    if isinstance(action_id, bool) or not isinstance(action_id, int):
        raise ValueError("action_id must be an integer")
    expected = action_id_for_path(path_name)
    if action_id != expected:
        raise ValueError(
            f"action_id {action_id} does not match path {path_name!r}; "
            f"expected {expected}"
        )
