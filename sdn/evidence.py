"""Controller-owned packet evidence accumulator."""

from __future__ import annotations

from collections import defaultdict, deque
import time
from typing import Any

from fleet.registry import get_drone


class ControllerEvidenceStore:
    """Keep cumulative OpenFlow totals and valid sample deltas separate."""

    def __init__(self, *, source: str, independent: bool):
        self.source = source
        self.independent = independent
        self._observations: dict[str, dict[str, Any]] = {}
        self._port_observations: dict[str, dict[str, Any]] = {}
        self._identities: dict[str, tuple[str, float]] = {}
        self._control_events: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=2048)
        )

    def record_control_message(self, drone_id: str, *, timestamp: float | None = None) -> None:
        """Count an access-port PacketIn, never an internal route API call."""
        self._control_events[drone_id].append(float(timestamp or time.time()))

    def update_flow_observation(
        self,
        drone_id: str,
        *,
        received_packets: int,
        forwarded_packets: int,
        dropped_packets: int,
        observed_source_mac: str,
        timestamp: float | None = None,
    ) -> None:
        observed_at = float(timestamp or time.time())
        counters = {
            "controller_rx_packets": max(0, int(received_packets)),
            "controller_forwarded_packets": max(0, int(forwarded_packets)),
            "controller_policy_dropped_packets": max(0, int(dropped_packets)),
        }
        previous = self._observations.get(drone_id)
        window: dict[str, Any] = {}
        if previous is not None:
            elapsed = observed_at - float(previous["controller_timestamp"])
            if 0.0 < elapsed <= 3.0 and all(
                counters[key] >= previous[key] for key in counters
            ):
                window = {
                    "controller_flow_window_rx_packets": (
                        counters["controller_rx_packets"]
                        - previous["controller_rx_packets"]
                    ),
                    "controller_flow_window_forwarded_packets": (
                        counters["controller_forwarded_packets"]
                        - previous["controller_forwarded_packets"]
                    ),
                    "controller_flow_window_policy_dropped_packets": (
                        counters["controller_policy_dropped_packets"]
                        - previous["controller_policy_dropped_packets"]
                    ),
                    "flow_window_start": float(previous["controller_timestamp"]),
                    "flow_window_seconds": elapsed,
                    "packet_rate_per_s": (
                        counters["controller_rx_packets"]
                        - previous["controller_rx_packets"]
                    ) / elapsed,
                }
        self._observations[drone_id] = {
            **counters,
            "observed_source_mac": str(observed_source_mac).lower(),
            "controller_timestamp": observed_at,
            "flow_totals_scope": "installed_rule_cumulative",
            **window,
        }

    def update_source_identity(
        self, drone_id: str, observed_source_mac: str, *, timestamp: float | None = None
    ) -> None:
        """Record a source identity observed on a registry-bound access port."""
        self._identities[drone_id] = (
            str(observed_source_mac).lower(), float(timestamp or time.time())
        )

    def update_port_observation(
        self,
        drone_id: str,
        *,
        received_packets: int,
        dropped_packets: int,
        error_packets: int,
        timestamp: float | None = None,
    ) -> None:
        """Keep physical ingress receive drops separate from installed flow drops."""
        observed_at = float(timestamp if timestamp is not None else time.time())
        counters = {
            "controller_port_rx_packets": max(0, int(received_packets)),
            "controller_port_dropped_packets": max(0, int(dropped_packets)),
            "controller_port_error_packets": max(0, int(error_packets)),
        }
        previous = self._port_observations.get(drone_id)
        window: dict[str, Any] = {}
        if previous is not None:
            elapsed = observed_at - float(previous["port_timestamp"])
            if 0.0 < elapsed <= 3.0 and all(
                counters[key] >= previous[key] for key in counters
            ):
                window = {
                    "controller_port_window_rx_packets": (
                        counters["controller_port_rx_packets"]
                        - previous["controller_port_rx_packets"]
                    ),
                    "controller_port_window_dropped_packets": (
                        counters["controller_port_dropped_packets"]
                        - previous["controller_port_dropped_packets"]
                    ),
                    "controller_port_window_error_packets": (
                        counters["controller_port_error_packets"]
                        - previous["controller_port_error_packets"]
                    ),
                    "port_window_seconds": elapsed,
                }
        self._port_observations[drone_id] = {
            **counters, "port_timestamp": observed_at, **window,
        }

    def snapshot(self, drone_id: str, *, now: float | None = None) -> dict[str, Any]:
        observed_now = float(now or time.time())
        record = self._observations.get(drone_id)
        if record is None:
            return {
                "available": False,
                "reason": "no_controller_packet_observation",
                "source": self.source,
                "independent": self.independent,
                "controller_timestamp": observed_now,
            }
        events = self._control_events[drone_id]
        while events and events[0] < observed_now - 1.0:
            events.popleft()
        drone = get_drone(drone_id)
        identity = self._identities.get(drone_id)
        observed_mac = record["observed_source_mac"]
        identity_observed_at = None
        if identity is not None and observed_now - identity[1] <= 3.0:
            observed_mac, identity_observed_at = identity
        supported_signals = ["packet_counters", "policy_drop_counters", "control_rate"]
        if "flow_window_seconds" in record:
            supported_signals.append("flow_sample_window")
        port = self._port_observations.get(drone_id)
        port_fields: dict[str, Any] = {}
        if port is not None and 0.0 <= observed_now - port["port_timestamp"] <= 3.0:
            port_fields = {
                **port,
                "controller_dropped_packets": (
                    port["controller_port_dropped_packets"]
                    + port["controller_port_error_packets"]
                ),
                "drop_counter_semantics": "ingress_port_receive_drop_error",
            }
            supported_signals.extend(["port_receive_drop_counters", "port_receive_errors"])
            if "port_window_seconds" in port:
                supported_signals.append("port_receive_drop_window")
        if identity_observed_at is not None:
            supported_signals.append("source_mac")
        return {
            "available": True,
            **record,
            **port_fields,
            "observed_source_mac": observed_mac,
            "identity_observed_at": identity_observed_at,
            "expected_source_mac": drone["mac"] if drone else None,
            "control_messages_per_s": float(len(events)),
            "control_rate_observer": "access_port_packet_in",
            "supported_signals": supported_signals,
            "source": self.source,
            "independent": self.independent,
        }
