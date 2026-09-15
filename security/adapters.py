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
    max_window_alignment_s: float = 0.5,
) -> dict[str, Any] | None:
    """Join labelled simulation evidence or provably comparable real windows."""
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
    require_window = category == "ryu_openflow" or bool(independent)
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
        optional: dict[str, Any] = {}
        if require_window:
            window_limit = finite(max_window_alignment_s, nonnegative=True)
            if window_limit <= 0 or window_limit > skew_limit:
                return None
            if reported.get("report_counter_scope") != "sample_window" or (
                controller.get("flow_totals_scope") != "installed_rule_cumulative"
            ):
                return None
            report_seconds = finite(reported["report_window_seconds"], nonnegative=True)
            flow_seconds = finite(controller["flow_window_seconds"], nonnegative=True)
            flow_start = finite(controller["flow_window_start"], nonnegative=True)
            report_start = reported_timestamp - report_seconds
            if (
                not 0 < report_seconds <= skew_limit
                or not 0 < flow_seconds <= skew_limit
                or abs((controller_timestamp - flow_start) - flow_seconds) > 0.001
                or abs(reported_timestamp - controller_timestamp) > window_limit
                or abs(report_start - flow_start) > window_limit
                or abs(report_seconds - flow_seconds) > window_limit
                or report_start < 0
            ):
                return None
            controller_rx = count(controller["controller_flow_window_rx_packets"])
            controller_forwarded = count(
                controller["controller_flow_window_forwarded_packets"]
            )
            policy_dropped = count(
                controller["controller_flow_window_policy_dropped_packets"]
            )
            if (
                reported_forwarded > reported_tx
                or controller_forwarded + policy_dropped > controller_rx
            ):
                return None
            optional.update({
                "counter_scope": "aligned_sample_window_v1",
                "report_window_seconds": report_seconds,
                "flow_window_seconds": flow_seconds,
                "report_window_start": report_start,
                "flow_window_start": flow_start,
                "window_alignment_s": max(
                    abs(reported_timestamp - controller_timestamp),
                    abs(report_start - flow_start),
                ),
                "controller_policy_dropped_packets": policy_dropped,
                "controller_policy_total_dropped_packets": count(
                    controller["controller_policy_dropped_packets"]
                ),
                "controller_flow_total_rx_packets": count(
                    controller["controller_rx_packets"]
                ),
                "controller_flow_total_forwarded_packets": count(
                    controller["controller_forwarded_packets"]
                ),
            })
        else:
            controller_rx = count(controller["controller_rx_packets"])
            controller_forwarded = count(controller["controller_forwarded_packets"])
            if controller.get("controller_policy_dropped_packets") is not None:
                optional["controller_policy_dropped_packets"] = count(
                    controller["controller_policy_dropped_packets"]
                )
        control_rate = finite(controller["control_messages_per_s"], nonnegative=True)
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
            "controller_port_rx_packets", "controller_port_dropped_packets",
            "controller_port_error_packets",
            "controller_port_window_rx_packets",
            "controller_port_window_dropped_packets",
            "controller_port_window_error_packets",
        ):
            if controller.get(counter_key) is not None:
                optional[counter_key] = count(controller[counter_key])
        if controller.get("port_timestamp") is not None:
            optional["port_timestamp"] = finite(
                controller["port_timestamp"], nonnegative=True
            )
        if controller.get("port_window_seconds") is not None:
            window_seconds = finite(
                controller["port_window_seconds"], nonnegative=True
            )
            if window_seconds <= 0:
                return None
            optional["port_window_seconds"] = window_seconds
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
    except (KeyError, TypeError, ValueError, OverflowError):
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
