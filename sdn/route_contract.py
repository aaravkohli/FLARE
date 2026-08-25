"""Shared route identifiers used by the orchestrator and SDN services."""

from __future__ import annotations

ROUTABLE_PATHS = ("direct", "satellite", "mesh")
FALLBACK_PATH = "fallback"
VALID_PATHS = frozenset((*ROUTABLE_PATHS, FALLBACK_PATH))
VALID_DRONES = frozenset(("drone_1", "drone_2", "drone_3"))

PATH_TO_ACTION = {
    "direct": 0,
    "satellite": 1,
    "mesh": 2,
    # The controller's emergency fallback uses the physical mesh port.
    "fallback": 2,
}


def normalize_installed_path(path_name: str) -> str:
    """Translate the controller-only fallback label to its physical route."""
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
