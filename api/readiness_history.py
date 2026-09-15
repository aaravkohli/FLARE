"""Bounded, process-local history for SDN readiness transitions."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import threading
import time
from typing import Iterable, Mapping, Optional


class ReadinessHistory:
    """Record meaningful readiness changes without storing every poll."""

    def __init__(
        self,
        *,
        max_entries: int,
        drone_ids: Iterable[str],
        route_names: Iterable[str],
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = int(max_entries)
        self.drone_ids = tuple(sorted(str(value) for value in drone_ids))
        self.route_names = tuple(str(value) for value in route_names)
        self._events: deque[dict] = deque(maxlen=self.max_entries)
        self._fingerprint: Optional[tuple] = None
        self._lock = threading.Lock()

    def set_drone_ids(self, drone_ids: Iterable[str]) -> None:
        """Refresh the fleet dimension after an authenticated enrollment."""
        normalized = tuple(sorted(str(value) for value in drone_ids))
        if not normalized:
            raise ValueError("drone_ids must contain at least one drone")
        with self._lock:
            if normalized != self.drone_ids:
                self.drone_ids = normalized
                self._fingerprint = None

    def _normalize(self, state: Mapping) -> dict:
        available = state.get("available_paths", {})
        if not isinstance(available, Mapping):
            available = {}
        available_paths = {}
        for drone_id in self.drone_ids:
            drone_paths = available.get(drone_id, [])
            if not isinstance(drone_paths, (list, tuple, set, frozenset)):
                drone_paths = []
            available_paths[drone_id] = [
                path for path in self.route_names if path in drone_paths
            ]
        unavailable_paths = {
            drone_id: [
                path
                for path in self.route_names
                if path not in available_paths[drone_id]
            ]
            for drone_id in self.drone_ids
        }
        return {
            "mode": str(state.get("mode", "unknown")),
            "ready": bool(state.get("ready", False)),
            "status": str(state.get("status", "unknown")),
            "connected_switches": max(0, int(state.get("connected_switches", 0))),
            "expected_switches": max(0, int(state.get("expected_switches", 0))),
            "available_paths": available_paths,
            "unavailable_paths": unavailable_paths,
            "error": str(state["error"]) if state.get("error") else None,
        }

    def _describe(self, state: Mapping) -> tuple[str, str]:
        if not state["ready"]:
            if state["status"] == "unreachable":
                return "critical", "SDN controller readiness endpoint is unreachable"
            switch_progress = ""
            if state["expected_switches"]:
                switch_progress = (
                    f" ({state['connected_switches']}/"
                    f"{state['expected_switches']} switches connected)"
                )
            return "critical", f"SDN data plane is not ready{switch_progress}"

        degraded = {
            drone_id: paths
            for drone_id, paths in state["unavailable_paths"].items()
            if paths
        }
        if degraded:
            details = "; ".join(
                f"{drone_id}: {', '.join(paths)}"
                for drone_id, paths in degraded.items()
            )
            return "warning", f"SDN route availability is degraded ({details})"
        return "info", "SDN data plane is ready with all routes available"

    def record(self, state: Mapping, *, timestamp: Optional[float] = None) -> dict:
        normalized = self._normalize(state)
        fingerprint = (
            normalized["mode"],
            normalized["ready"],
            normalized["status"],
            normalized["connected_switches"],
            normalized["expected_switches"],
            tuple(
                (drone_id, tuple(paths))
                for drone_id, paths in normalized["available_paths"].items()
            ),
            normalized["error"],
        )

        with self._lock:
            if fingerprint != self._fingerprint:
                severity, message = self._describe(normalized)
                self._events.append({
                    "timestamp": float(time.time() if timestamp is None else timestamp),
                    "severity": severity,
                    "message": message,
                    **normalized,
                })
                self._fingerprint = fingerprint
            return deepcopy(self._events[-1])

    def snapshot(self, *, limit: int) -> list[dict]:
        bounded_limit = max(1, min(int(limit), self.max_entries))
        with self._lock:
            return deepcopy(list(reversed(self._events))[:bounded_limit])

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._fingerprint = None
