"""Join participant-reported and independently observed controller evidence."""

from __future__ import annotations

import math
import time
from typing import Any, Mapping


def merge_security_evidence(
    reported: Mapping[str, Any] | None,
    controller: Mapping[str, Any] | None,
    *,
    category: str,
    observer_id: str,
    independent: bool,
    collected_at: float | None = None,
    max_join_skew_s: float = 3.0,
) -> dict[str, Any] | None:
    """Return the strict v2 evidence shape or ``None`` when it is incomplete."""
    reported = dict(reported or {})
    controller = dict(controller or {})
    required_reported = ("reported_tx_packets", "reported_forwarded_packets", "timestamp")
    required_controller = (
        "controller_rx_packets",
        "controller_forwarded_packets",
        "control_messages_per_s",
        "controller_timestamp",
    )
    if any(key not in reported for key in required_reported):
        return None
    if any(key not in controller for key in required_controller):
        return None
    def count(value: Any) -> int:
        number = float(value)
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            raise ValueError("security packet counter is invalid")
        return int(number)

    def finite(value: Any, *, nonnegative: bool = False) -> float:
        number = float(value)
        if not math.isfinite(number) or nonnegative and number < 0:
            raise ValueError("security evidence numeric value is invalid")
        return number

    try:
        skew_limit = finite(max_join_skew_s, nonnegative=True)
        if skew_limit <= 0:
            return None
        observed_at = finite(collected_at if collected_at is not None else time.time())
        reported_timestamp = finite(reported["timestamp"], nonnegative=True)
        controller_timestamp = finite(controller["controller_timestamp"], nonnegative=True)
        if abs(reported_timestamp - controller_timestamp) > skew_limit:
            return None
        reported_tx = count(reported["reported_tx_packets"])
        reported_forwarded = count(reported["reported_forwarded_packets"])
        controller_rx = count(controller["controller_rx_packets"])
        controller_forwarded = count(controller["controller_forwarded_packets"])
        control_rate = finite(controller["control_messages_per_s"], nonnegative=True)
        optional: dict[str, Any] = {}
        if controller.get("duplicate_sequence_ratio") is not None:
            duplicate = finite(controller["duplicate_sequence_ratio"], nonnegative=True)
            if duplicate > 1.0:
                return None
            optional["duplicate_sequence_ratio"] = duplicate
        if controller.get("controller_dropped_packets") is not None:
            optional["controller_dropped_packets"] = count(
                controller["controller_dropped_packets"]
            )
        for counter_key in (
            "controller_policy_dropped_packets", "controller_port_rx_packets",
            "controller_port_dropped_packets", "controller_port_error_packets",
        ):
            if controller.get(counter_key) is not None:
                optional[counter_key] = count(controller[counter_key])
        if controller.get("port_timestamp") is not None:
            optional["port_timestamp"] = finite(
                controller["port_timestamp"], nonnegative=True
            )
        if controller.get("drop_counter_semantics") is not None:
            optional["drop_counter_semantics"] = str(
                controller["drop_counter_semantics"]
            )
        if controller.get("packet_rate_per_s") is not None:
            optional["packet_rate_per_s"] = finite(
                controller["packet_rate_per_s"], nonnegative=True
            )
        if controller.get("identity_observed_at") is not None:
            optional["identity_observed_at"] = finite(
                controller["identity_observed_at"], nonnegative=True
            )
    except (TypeError, ValueError, OverflowError):
        return None
    merged = {
        "reported_tx_packets": reported_tx,
        "controller_rx_packets": controller_rx,
        "controller_forwarded_packets": controller_forwarded,
        "reported_forwarded_packets": reported_forwarded,
        "control_messages_per_s": control_rate,
        "timestamp": reported_timestamp,
        "controller_timestamp": controller_timestamp,
        "simulation_profile": reported.get("simulation_profile"),
        "observed_source_mac": controller.get("observed_source_mac"),
        "expected_source_mac": controller.get("expected_source_mac"),
        "control_rate_observer": controller.get("control_rate_observer"),
        "provenance": {
            "category": category,
            "observer_id": observer_id,
            "independent": bool(independent),
            "collected_at": observed_at,
            "age_s": max(0.0, observed_at - controller_timestamp),
        },
        **optional,
    }
    return merged
