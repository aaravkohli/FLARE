"""Regression coverage for packet-level ns-3 trace ingestion."""

from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from rl.ns3_trace import (
    NS3_RAW_VERSION,
    NS3_SOURCE_VERSION,
    NS3_TOPOLOGY,
    Ns3TraceValidationError,
    convert_ns3_packet_trace,
    load_ns3_packet_trace,
    raw_trace_fingerprint,
    validate_ns3_packet_trace,
)
from rl.env import SynchronizedTraceDronePathEnv
from rl.traces import TRACE_VERSION, validate_synchronized_trace, write_synchronized_trace


def _path(
    name: str,
    *,
    tx: int = 100,
    rx: int = 90,
    delay_ms: float = 10.0,
    jammed: int = 0,
) -> dict:
    rx_bytes = rx * 1024
    return {
        "name": name,
        "tx_packets": tx,
        "rx_packets": rx,
        "rx_bytes": rx_bytes,
        "packet_loss": 1.0 - rx / tx,
        "mean_delay_ms": delay_ms,
        "throughput_mbps": rx_bytes * 8.0 / 1_000_000.0,
        "jammed": jammed,
    }


def _payload() -> dict:
    return {
        "raw_version": NS3_RAW_VERSION,
        "simulator": "ns-3",
        "topology": NS3_TOPOLOGY,
        "scenario": "persistent_spot",
        "seed": 700,
        "run": 3,
        "episode_id": "persistent_spot:ns3:seed=700:run=00003",
        "interval_s": 1.0,
        "packet_size_bytes": 1024,
        "offered_packets_per_second": 100.0,
        "samples": [
            {
                "step": 0,
                "timestamp_s": 1.0,
                "paths": [
                    _path("direct", rx=20, jammed=1),
                    _path("satellite", rx=95, delay_ms=120.0),
                    _path("mesh", rx=97, delay_ms=35.0),
                ],
            },
            {
                "step": 1,
                "timestamp_s": 2.0,
                "paths": [
                    _path("direct", rx=25, jammed=1),
                    _path("satellite", rx=96, delay_ms=120.0),
                    _path("mesh", rx=98, delay_ms=35.0),
                ],
            },
        ],
    }


def test_packet_trace_validation_returns_canonical_path_order():
    payload = _payload()
    payload["samples"][0]["paths"].reverse()

    validated = validate_ns3_packet_trace(payload)

    assert [path["name"] for path in validated["samples"][0]["paths"]] == [
        "direct",
        "satellite",
        "mesh",
    ]


def test_packet_trace_rejects_counter_and_derived_metric_mismatch():
    payload = _payload()
    payload["samples"][0]["paths"][0]["packet_loss"] = 0.1

    with pytest.raises(Ns3TraceValidationError, match="packet counters"):
        validate_ns3_packet_trace(payload)

    payload = _payload()
    payload["samples"][0]["paths"][0]["rx_packets"] = 101
    with pytest.raises(Ns3TraceValidationError, match="cannot exceed"):
        validate_ns3_packet_trace(payload)

    payload = _payload()
    payload["samples"][0]["paths"][0]["rx_bytes"] -= 1
    with pytest.raises(Ns3TraceValidationError, match="packet_size_bytes"):
        validate_ns3_packet_trace(payload)


def test_packet_trace_rejects_missing_duplicate_and_gapped_data():
    payload = _payload()
    payload["samples"][0]["paths"].pop()
    with pytest.raises(Ns3TraceValidationError, match="exactly three"):
        validate_ns3_packet_trace(payload)

    payload = _payload()
    payload["samples"][0]["paths"][2]["name"] = "direct"
    with pytest.raises(Ns3TraceValidationError, match="duplicate path"):
        validate_ns3_packet_trace(payload)

    payload = _payload()
    payload["samples"][1]["step"] = 3
    with pytest.raises(Ns3TraceValidationError, match="contiguous"):
        validate_ns3_packet_trace(payload)


def test_packet_conversion_uses_explicit_qos_risk_and_raw_provenance():
    payload = _payload()

    frame = convert_ns3_packet_trace(payload)

    assert set(frame["trace_version"]) == {TRACE_VERSION}
    assert set(frame["source"].str.startswith(NS3_SOURCE_VERSION)) == {True}
    assert set(frame["episode_id"]) == {payload["episode_id"]}
    first = frame.iloc[0]
    assert first["direct_latency_norm"] == pytest.approx(0.1)
    assert first["direct_packet_loss"] == pytest.approx(0.8)
    assert first["direct_threat_score"] == pytest.approx(0.59)
    assert first["satellite_latency_norm"] == pytest.approx(0.4)
    assert first["direct_jammed"] == 1
    validate_synchronized_trace(frame)


def test_raw_fingerprint_is_independent_of_json_formatting(tmp_path):
    payload = _payload()
    compact = tmp_path / "compact.json"
    pretty = tmp_path / "pretty.json"
    compact.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    pretty.write_text(json.dumps(payload, indent=4), encoding="utf-8")

    compact_payload = load_ns3_packet_trace(compact)
    pretty_payload = load_ns3_packet_trace(pretty)

    assert raw_trace_fingerprint(compact_payload) == raw_trace_fingerprint(
        pretty_payload
    )


def test_packet_trace_rejects_unknown_contract_version():
    payload = copy.deepcopy(_payload())
    payload["raw_version"] = "legacy"

    with pytest.raises(Ns3TraceValidationError, match="raw_version"):
        validate_ns3_packet_trace(payload)


def test_trace_environment_can_filter_a_mixed_scenario_file(tmp_path):
    spot = convert_ns3_packet_trace(_payload())
    reactive_payload = _payload()
    reactive_payload["scenario"] = "reactive"
    reactive_payload["episode_id"] = "reactive:ns3:seed=700:run=00004"
    reactive_payload["run"] = 4
    reactive = convert_ns3_packet_trace(reactive_payload)
    mixed_path = write_synchronized_trace(
        pd.concat([spot, reactive], ignore_index=True),
        tmp_path / "mixed.csv",
    )

    environment = SynchronizedTraceDronePathEnv(mixed_path, scenario="reactive")
    _, info = environment.reset(seed=0)

    assert info["scenario"] == "reactive"
    assert info["episode_id"].startswith("reactive:")

    missing = SynchronizedTraceDronePathEnv(mixed_path, scenario="barrage")
    with pytest.raises(ValueError, match="does not contain scenario"):
        missing.reset(seed=0)
