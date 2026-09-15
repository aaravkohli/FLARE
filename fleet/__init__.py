"""Canonical dynamic fleet enrollment for FLARE."""

from .registry import (
    DroneRegistrationError,
    active_drone_ids,
    get_drone,
    is_active_drone,
    list_drones,
    register_drone,
)

__all__ = [
    "DroneRegistrationError",
    "active_drone_ids",
    "get_drone",
    "is_active_drone",
    "list_drones",
    "register_drone",
]
