"""Packet-study results must be derived from receiver counters, not displays."""

from __future__ import annotations

from copy import deepcopy

import pytest

from pathlib import Path

from scripts.run_wifi_mobility_study import run_study, validate_packet_result


def _payload() -> dict:
    return {
        "schema_version": "flare_ns3_wifi_mobility_v1",
        "evidence_category": "packet_simulation",
        "topology": "two_node_80211b_adhoc_log_distance_v1",
        "scenario": "moving_away",
        "seed": 7,
        "run": 1,
        "samples": [
            {
                "step": 1, "time_seconds": 1.0, "distance_meters": 27.0,
                "offered_packets": 40, "received_packets": 40,
                "cumulative_offered_packets": 40, "cumulative_received_packets": 40,
                "pdr": 1.0,
            },
            {
                "step": 2, "time_seconds": 2.0, "distance_meters": 39.0,
                "offered_packets": 50, "received_packets": 20,
                "cumulative_offered_packets": 90, "cumulative_received_packets": 60,
                "pdr": 0.666667,
            },
        ],
    }


def test_receiver_derived_cumulative_pdr_contract() -> None:
    assert validate_packet_result(_payload(), scenario="moving_away", seed=7, steps=2) == 0.666667


@pytest.mark.parametrize("field,value,reason", [
    ("pdr", 0.9, "derived from receiver"),
    ("cumulative_received_packets", 91, "time/count/delivery"),
    ("distance_meters", 20.0, "did not move away"),
    ("offered_packets", 49, "interval and cumulative"),
])
def test_false_or_inconsistent_packet_metrics_fail(field, value, reason) -> None:
    payload = deepcopy(_payload())
    payload["samples"][1][field] = value
    with pytest.raises(ValueError, match=reason):
        validate_packet_result(payload, scenario="moving_away", seed=7, steps=2)


def test_missing_sample_and_wrong_category_fail() -> None:
    payload = _payload()
    with pytest.raises(ValueError, match="incomplete"):
        validate_packet_result(payload, scenario="moving_away", seed=7, steps=3)
    payload["evidence_category"] = "hardware"
    with pytest.raises(ValueError, match="packet-simulation"):
        validate_packet_result(payload, scenario="moving_away", seed=7, steps=2)


def test_protocol_inputs_cannot_change_without_versioning(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frozen"):
        run_study(
            ns3_dir=tmp_path, seeds=[7], steps=12, interval=1.0,
            initial_distance=15.0, speed=12.0,
        )
