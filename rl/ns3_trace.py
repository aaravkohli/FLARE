"""Validate packet-level ns-3 output and convert it to the RL trace contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from rl.reward import MAX_LATENCY_MS, PATH_NAMES
from rl.traces import (
    TRACE_COLUMNS,
    TRACE_VERSION,
    SUPPORTED_SCENARIOS,
    trace_fingerprint,
    validate_synchronized_trace,
    write_synchronized_trace,
)


NS3_RAW_VERSION = "flare_ns3_packet_trace_v1"
NS3_TOPOLOGY = "independent_point_to_point_three_path_v1"
NS3_SOURCE_VERSION = "ns3:packet-level:qos-risk-v1"
THREAT_LOSS_WEIGHT = 0.7
THREAT_LATENCY_WEIGHT = 0.3


class Ns3TraceValidationError(ValueError):
    """Raised when ns-3 output violates the packet-trace contract."""


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Ns3TraceValidationError(f"{name} must be an object")
    return value


def _finite_number(value: object, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise Ns3TraceValidationError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Ns3TraceValidationError(f"{name} must be a finite number") from exc
    if not math.isfinite(number) or number < minimum:
        raise Ns3TraceValidationError(
            f"{name} must be finite and at least {minimum}"
        )
    return number


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    number = _finite_number(value, name, minimum=float(minimum))
    if not number.is_integer():
        raise Ns3TraceValidationError(f"{name} must be an integer")
    return int(number)


def raw_trace_fingerprint(payload: Mapping[str, Any]) -> str:
    """Fingerprint parsed raw content independently of JSON whitespace."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_ns3_packet_trace(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical copy of one synchronized packet-level episode."""
    root = _require_mapping(payload, "packet trace")
    if root.get("raw_version") != NS3_RAW_VERSION:
        raise Ns3TraceValidationError(
            f"raw_version must be exactly {NS3_RAW_VERSION!r}"
        )
    if root.get("simulator") != "ns-3":
        raise Ns3TraceValidationError("simulator must be exactly 'ns-3'")
    if root.get("topology") != NS3_TOPOLOGY:
        raise Ns3TraceValidationError(
            f"topology must be exactly {NS3_TOPOLOGY!r}"
        )

    scenario = str(root.get("scenario", "")).strip()
    if scenario not in SUPPORTED_SCENARIOS:
        raise Ns3TraceValidationError(
            f"scenario must be one of {SUPPORTED_SCENARIOS}, got {scenario!r}"
        )
    episode_id = str(root.get("episode_id", "")).strip()
    if not episode_id:
        raise Ns3TraceValidationError("episode_id must be a non-empty string")
    seed = _integer(root.get("seed"), "seed", minimum=1)
    run = _integer(root.get("run"), "run", minimum=1)
    interval_s = _finite_number(root.get("interval_s"), "interval_s", minimum=1e-9)
    packet_size = _integer(
        root.get("packet_size_bytes"), "packet_size_bytes", minimum=1
    )
    offered_rate = _finite_number(
        root.get("offered_packets_per_second"),
        "offered_packets_per_second",
        minimum=1e-9,
    )
    expected_packets_per_interval_value = offered_rate * interval_s
    expected_packets_per_interval = round(expected_packets_per_interval_value)
    if not math.isclose(
        expected_packets_per_interval_value,
        expected_packets_per_interval,
        abs_tol=1e-9,
    ):
        raise Ns3TraceValidationError(
            "offered_packets_per_second * interval_s must be an integer"
        )

    samples = root.get("samples")
    if not isinstance(samples, list) or not samples:
        raise Ns3TraceValidationError("samples must be a non-empty array")

    canonical_samples: list[dict[str, Any]] = []
    for expected_step, sample_value in enumerate(samples):
        sample = _require_mapping(sample_value, f"samples[{expected_step}]")
        step = _integer(sample.get("step"), f"samples[{expected_step}].step")
        if step != expected_step:
            raise Ns3TraceValidationError(
                "sample steps must be contiguous and start at zero"
            )
        timestamp_s = _finite_number(
            sample.get("timestamp_s"), f"samples[{step}].timestamp_s"
        )
        expected_timestamp = (step + 1) * interval_s
        if not math.isclose(timestamp_s, expected_timestamp, abs_tol=1e-6):
            raise Ns3TraceValidationError(
                f"samples[{step}].timestamp_s must equal {expected_timestamp}"
            )

        paths = sample.get("paths")
        if not isinstance(paths, list) or len(paths) != len(PATH_NAMES):
            raise Ns3TraceValidationError(
                f"samples[{step}].paths must contain exactly three paths"
            )
        by_name: dict[str, dict[str, Any]] = {}
        for path_value in paths:
            path = _require_mapping(path_value, f"samples[{step}].paths[]")
            path_name = str(path.get("name", "")).strip()
            if path_name in by_name:
                raise Ns3TraceValidationError(
                    f"samples[{step}] contains duplicate path {path_name!r}"
                )
            if path_name not in PATH_NAMES:
                raise Ns3TraceValidationError(
                    f"samples[{step}] contains unknown path {path_name!r}"
                )

            prefix = f"samples[{step}].paths[{path_name}]"
            tx_packets = _integer(path.get("tx_packets"), f"{prefix}.tx_packets", minimum=1)
            rx_packets = _integer(path.get("rx_packets"), f"{prefix}.rx_packets")
            rx_bytes = _integer(path.get("rx_bytes"), f"{prefix}.rx_bytes")
            if tx_packets != expected_packets_per_interval:
                raise Ns3TraceValidationError(
                    f"{prefix}.tx_packets does not match the configured offered rate"
                )
            if rx_packets > tx_packets:
                raise Ns3TraceValidationError(
                    f"{prefix}.rx_packets cannot exceed tx_packets"
                )
            if (rx_packets == 0) != (rx_bytes == 0):
                raise Ns3TraceValidationError(
                    f"{prefix}.rx_bytes must be zero exactly when rx_packets is zero"
                )
            if rx_bytes != rx_packets * packet_size:
                raise Ns3TraceValidationError(
                    f"{prefix}.rx_bytes does not match rx_packets * packet_size_bytes"
                )

            packet_loss = _finite_number(
                path.get("packet_loss"), f"{prefix}.packet_loss"
            )
            if packet_loss > 1.0:
                raise Ns3TraceValidationError(f"{prefix}.packet_loss must be in [0, 1]")
            expected_loss = 1.0 - rx_packets / tx_packets
            if not math.isclose(packet_loss, expected_loss, abs_tol=1e-6):
                raise Ns3TraceValidationError(
                    f"{prefix}.packet_loss does not match packet counters"
                )

            mean_delay_ms = _finite_number(
                path.get("mean_delay_ms"), f"{prefix}.mean_delay_ms"
            )
            if rx_packets > 0 and mean_delay_ms <= 0.0:
                raise Ns3TraceValidationError(
                    f"{prefix}.mean_delay_ms must be positive when packets arrive"
                )
            if rx_packets == 0 and mean_delay_ms != 0.0:
                raise Ns3TraceValidationError(
                    f"{prefix}.mean_delay_ms must be zero when no packets arrive"
                )
            throughput_mbps = _finite_number(
                path.get("throughput_mbps"), f"{prefix}.throughput_mbps"
            )
            expected_throughput = rx_bytes * 8.0 / (interval_s * 1_000_000.0)
            if not math.isclose(
                throughput_mbps,
                expected_throughput,
                abs_tol=1e-6,
            ):
                raise Ns3TraceValidationError(
                    f"{prefix}.throughput_mbps does not match rx_bytes"
                )
            jammed = path.get("jammed")
            if isinstance(jammed, bool):
                jammed_value = int(jammed)
            elif jammed in (0, 1):
                jammed_value = int(jammed)
            else:
                raise Ns3TraceValidationError(f"{prefix}.jammed must be 0 or 1")

            by_name[path_name] = {
                "name": path_name,
                "tx_packets": tx_packets,
                "rx_packets": rx_packets,
                "rx_bytes": rx_bytes,
                "packet_loss": packet_loss,
                "mean_delay_ms": mean_delay_ms,
                "throughput_mbps": throughput_mbps,
                "jammed": jammed_value,
            }

        missing_paths = set(PATH_NAMES) - set(by_name)
        if missing_paths:
            raise Ns3TraceValidationError(
                f"samples[{step}] is missing paths {sorted(missing_paths)}"
            )
        canonical_samples.append(
            {
                "step": step,
                "timestamp_s": timestamp_s,
                "paths": [by_name[path_name] for path_name in PATH_NAMES],
            }
        )

    return {
        "raw_version": NS3_RAW_VERSION,
        "simulator": "ns-3",
        "topology": NS3_TOPOLOGY,
        "scenario": scenario,
        "seed": seed,
        "run": run,
        "episode_id": episode_id,
        "interval_s": interval_s,
        "packet_size_bytes": packet_size,
        "offered_packets_per_second": offered_rate,
        "samples": canonical_samples,
    }


def load_ns3_packet_trace(json_path: str | Path) -> dict[str, Any]:
    path = Path(json_path)
    if not path.is_file():
        raise FileNotFoundError(f"ns-3 packet trace not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Ns3TraceValidationError(f"invalid JSON in {path}: {exc}") from exc
    return validate_ns3_packet_trace(payload)


def convert_ns3_packet_trace(payload: Mapping[str, Any]) -> pd.DataFrame:
    """Convert one validated raw episode into synchronized routing rows.

    ns-3 does not run the federated RF classifier. The required threat field is
    therefore an explicit QoS-risk proxy: 70% observed loss plus 30% normalized
    mean delay. The source string records this distinction and the raw digest.
    """
    validated = validate_ns3_packet_trace(payload)
    raw_sha256 = raw_trace_fingerprint(validated)
    source = f"{NS3_SOURCE_VERSION}:raw-sha256={raw_sha256}"
    rows: list[dict[str, object]] = []
    for sample in validated["samples"]:
        row: dict[str, object] = {
            "trace_version": TRACE_VERSION,
            "source": source,
            "scenario": validated["scenario"],
            "generation_seed": validated["seed"],
            "episode_id": validated["episode_id"],
            "step": sample["step"],
        }
        for path_index, path_name in enumerate(PATH_NAMES):
            path = sample["paths"][path_index]
            latency_norm = float(
                np.clip(path["mean_delay_ms"] / MAX_LATENCY_MS[path_index], 0.0, 1.0)
            )
            packet_loss = float(path["packet_loss"])
            threat_score = float(
                np.clip(
                    THREAT_LOSS_WEIGHT * packet_loss
                    + THREAT_LATENCY_WEIGHT * latency_norm,
                    0.0,
                    1.0,
                )
            )
            row[f"{path_name}_threat_score"] = round(threat_score, 6)
            row[f"{path_name}_latency_norm"] = round(latency_norm, 6)
            row[f"{path_name}_packet_loss"] = round(packet_loss, 6)
            row[f"{path_name}_jammed"] = int(path["jammed"])
        rows.append(row)
    return validate_synchronized_trace(pd.DataFrame(rows, columns=TRACE_COLUMNS))


def convert_ns3_packet_trace_files(paths: Sequence[str | Path]) -> pd.DataFrame:
    if not paths:
        raise ValueError("at least one ns-3 packet trace is required")
    frames = [convert_ns3_packet_trace(load_ns3_packet_trace(path)) for path in paths]
    return validate_synchronized_trace(pd.concat(frames, ignore_index=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert packet-level ns-3 JSON into synchronized RL trace CSV"
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    frame = convert_ns3_packet_trace_files(args.inputs)
    output = write_synchronized_trace(frame, args.output)
    print(
        f"Wrote {len(frame)} packet-derived synchronized rows to {output} "
        f"[sha256:{trace_fingerprint(frame)[:12]}]"
    )


if __name__ == "__main__":
    main()
