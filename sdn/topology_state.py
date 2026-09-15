"""Fail-closed OpenFlow topology and link-availability tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from fleet.registry import list_drones
from sdn.route_contract import ROUTABLE_PATHS


@dataclass(frozen=True)
class RouteAvailability:
    available: bool
    reasons: tuple[str, ...]


def port_descriptor_is_up(
    descriptor,
    *,
    port_down_mask: int,
    unusable_state_mask: int,
) -> bool:
    """Interpret OpenFlow port configuration/state flags."""
    config = int(getattr(descriptor, "config", 0))
    state = int(getattr(descriptor, "state", 0))
    return not (config & port_down_mask or state & unusable_state_mask)


class TopologyState:
    """Tracks required switches, port inventories, and per-drone routes."""

    def __init__(
        self,
        *,
        expected_dpids: Iterable[int],
        ingress_dpid: int,
        egress_dpid: int,
        base_station_port: int,
        transit_dpids: Mapping[str, int],
        path_ports: Mapping[str, int],
        drone_access_ports: Mapping[str, int],
    ) -> None:
        self.expected_dpids = frozenset(int(dpid) for dpid in expected_dpids)
        self.ingress_dpid = int(ingress_dpid)
        self.egress_dpid = int(egress_dpid)
        self.base_station_port = int(base_station_port)
        self.transit_dpids = {
            path: int(dpid) for path, dpid in transit_dpids.items()
        }
        self.path_ports = {path: int(port) for path, port in path_ports.items()}
        self.drone_access_ports = {
            drone: int(port) for drone, port in drone_access_ports.items()
        }

        if set(self.transit_dpids) != set(ROUTABLE_PATHS):
            raise ValueError("transit_dpids must define every routable path")
        if set(self.path_ports) != set(ROUTABLE_PATHS):
            raise ValueError("path_ports must define every routable path")
        if not self.drone_access_ports:
            raise ValueError("drone_access_ports must define at least one drone")

        self._connected: set[int] = set()
        self._inventory_complete: set[int] = set()
        self._ports_up: dict[int, dict[int, bool]] = {}

    @classmethod
    def from_config(cls, config: Mapping) -> "TopologyState":
        topology = config["topology"]
        return cls(
            expected_dpids=topology["expected_dpids"],
            ingress_dpid=topology["ingress_dpid"],
            egress_dpid=topology["egress_dpid"],
            base_station_port=topology["base_station_port"],
            transit_dpids=topology["transit_dpids"],
            path_ports={
                path: config["port_map"][path]
                for path in ROUTABLE_PATHS
            },
            drone_access_ports={
                record["drone_id"]: record["access_port"]
                for record in list_drones(enabled_only=True)
            },
        )

    def connect(self, dpid: int, *, reset_inventory: bool = False) -> None:
        normalized = int(dpid)
        self._connected.add(normalized)
        if reset_inventory:
            self._inventory_complete.discard(normalized)
            self._ports_up.pop(normalized, None)

    def disconnect(self, dpid: Optional[int]) -> None:
        if dpid is None:
            return
        normalized = int(dpid)
        self._connected.discard(normalized)
        self._inventory_complete.discard(normalized)
        self._ports_up.pop(normalized, None)

    def set_port_inventory(self, dpid: int, ports_up: Mapping[int, bool]) -> None:
        normalized = int(dpid)
        if normalized not in self._connected:
            self.connect(normalized)
        self._ports_up[normalized] = {
            int(port): bool(is_up) for port, is_up in ports_up.items()
        }
        self._inventory_complete.add(normalized)

    def set_port_state(self, dpid: int, port: int, is_up: bool) -> None:
        normalized = int(dpid)
        self._ports_up.setdefault(normalized, {})[int(port)] = bool(is_up)

    @property
    def ready(self) -> bool:
        return (
            self.expected_dpids.issubset(self._connected)
            and self.expected_dpids.issubset(self._inventory_complete)
        )

    def _required_ports(self, path_name: str, drone_id: str) -> dict[int, set[int]]:
        self._sync_registered_drones()
        if path_name not in ROUTABLE_PATHS:
            raise ValueError(f"Unknown route: {path_name!r}")
        if drone_id not in self.drone_access_ports:
            raise ValueError(f"Unknown drone: {drone_id!r}")

        path_port = self.path_ports[path_name]
        transit_dpid = self.transit_dpids[path_name]
        return {
            self.ingress_dpid: {
                path_port,
                self.drone_access_ports[drone_id],
            },
            transit_dpid: {1, 2},
            self.egress_dpid: {path_port, self.base_station_port},
        }

    def _sync_registered_drones(self) -> None:
        """Refresh access-port bindings from the canonical fleet registry."""
        self.drone_access_ports = {
            record["drone_id"]: int(record["access_port"])
            for record in list_drones(enabled_only=True)
        }

    def route_status(self, path_name: str, drone_id: str) -> RouteAvailability:
        reasons: list[str] = []
        for dpid, required_ports in self._required_ports(path_name, drone_id).items():
            if dpid not in self._connected:
                reasons.append(f"switch_disconnected:{dpid}")
                continue
            if dpid not in self._inventory_complete:
                reasons.append(f"port_inventory_pending:{dpid}")
                continue
            known_ports = self._ports_up.get(dpid, {})
            for port in sorted(required_ports):
                if port not in known_ports:
                    reasons.append(f"port_missing:{dpid}:{port}")
                elif not known_ports[port]:
                    reasons.append(f"port_down:{dpid}:{port}")
        return RouteAvailability(available=not reasons, reasons=tuple(reasons))

    def available_paths(self, drone_id: str) -> list[str]:
        return [
            path
            for path in ROUTABLE_PATHS
            if self.route_status(path, drone_id).available
        ]

    def snapshot(self) -> dict:
        self._sync_registered_drones()
        return {
            "ready": self.ready,
            "expected_switches": sorted(self.expected_dpids),
            "connected_switches": sorted(self._connected),
            "inventory_complete_switches": sorted(self._inventory_complete),
            "ports": {
                str(dpid): {
                    str(port): "up" if is_up else "down"
                    for port, is_up in sorted(ports.items())
                }
                for dpid, ports in sorted(self._ports_up.items())
            },
            "available_paths": {
                drone: self.available_paths(drone)
                for drone in sorted(self.drone_access_ports)
            },
        }
