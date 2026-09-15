#!/usr/bin/env python3
"""Run a controlled two-node ns-3 Wi-Fi mobility packet study."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from provenance import write_experiment_run  # noqa: E402


SCHEMA = "flare_ns3_wifi_mobility_v1"
SOURCE = BASE / "simulation" / "ns3" / "flare_wifi_mobility.cc"
MODULES = (
    "applications", "wifi", "mobility", "propagation", "internet", "network", "core",
)
SCENARIOS = ("stationary", "moving_away")
FROZEN_PROTOCOL = {
    "seeds": [7, 42, 99],
    "steps": 12,
    "interval_seconds": 1.0,
    "initial_distance_meters": 15.0,
    "speed_mps": 12.0,
}


def _library(directory: Path, module: str) -> Path:
    candidates = [
        path for path in directory.glob(f"libns3-*-{module}-*")
        if path.is_file() and (".dylib" in path.name or ".so" in path.name)
    ]
    if not candidates:
        raise FileNotFoundError(f"ns-3 {module} library is missing from {directory}")
    return min(candidates, key=lambda path: (len(path.name), path.name))


def _compile(ns3_dir: Path, binary: Path) -> list[str]:
    include = ns3_dir / "build" / "include"
    libraries = ns3_dir / "build" / "lib"
    if not SOURCE.is_file() or not include.is_dir() or not libraries.is_dir():
        raise FileNotFoundError("Wi-Fi source or configured ns-3 build is unavailable")
    compiler = os.environ.get("CXX") or shutil.which("c++")
    if not compiler:
        raise FileNotFoundError("C++ compiler is unavailable")
    command = [
        compiler, "-std=c++20", "-O2", f"-I{include}", str(SOURCE),
        "-o", str(binary),
        *(str(_library(libraries, module)) for module in MODULES),
        f"-Wl,-rpath,{libraries}",
    ]
    subprocess.run(command, check=True, cwd=BASE)
    return command


def validate_packet_result(
    payload: dict[str, Any], *, scenario: str, seed: int, steps: int,
) -> float:
    if payload.get("schema_version") != SCHEMA:
        raise ValueError("unsupported Wi-Fi result schema")
    if payload.get("evidence_category") != "packet_simulation":
        raise ValueError("Wi-Fi result must retain packet-simulation provenance")
    if payload.get("topology") != "two_node_80211b_adhoc_log_distance_v1":
        raise ValueError("Wi-Fi topology contract changed")
    if payload.get("scenario") != scenario or payload.get("seed") != seed:
        raise ValueError("Wi-Fi result scenario/seed does not match its command")
    if payload.get("run") != 1:
        raise ValueError("Wi-Fi result random-stream run does not match the frozen protocol")
    samples = payload.get("samples")
    if not isinstance(samples, list) or len(samples) != steps:
        raise ValueError("Wi-Fi result has an incomplete sample sequence")
    previous_time = 0.0
    previous_distance = None
    previous_offered = 0
    previous_received = 0
    for index, sample in enumerate(samples, start=1):
        if sample.get("step") != index:
            raise ValueError("Wi-Fi sample steps are not contiguous")
        time_seconds = float(sample["time_seconds"])
        distance = float(sample["distance_meters"])
        offered = int(sample["cumulative_offered_packets"])
        received = int(sample["cumulative_received_packets"])
        pdr = float(sample["pdr"])
        if not all(math.isfinite(value) for value in (time_seconds, distance, pdr)):
            raise ValueError("Wi-Fi sample contains non-finite values")
        if (
            time_seconds <= previous_time or distance < 0
            or offered < previous_offered or received < previous_received
            or received > offered or not 0 <= pdr <= 1
        ):
            raise ValueError("Wi-Fi sample violates time/count/delivery contracts")
        if int(sample["offered_packets"]) != offered - previous_offered:
            raise ValueError("Wi-Fi offered interval and cumulative counters differ")
        if int(sample["received_packets"]) != received - previous_received:
            raise ValueError("Wi-Fi received interval and cumulative counters differ")
        expected_pdr = received / offered if offered else 0.0
        if abs(pdr - expected_pdr) > 1e-5:
            raise ValueError("Wi-Fi PDR is not derived from receiver packet counts")
        if previous_distance is not None:
            if scenario == "stationary" and abs(distance - previous_distance) > 1e-6:
                raise ValueError("stationary Wi-Fi node moved")
            if scenario == "moving_away" and distance <= previous_distance:
                raise ValueError("moving-away Wi-Fi node did not move away")
        previous_time = time_seconds
        previous_distance = distance
        previous_offered = offered
        previous_received = received
    return float(samples[-1]["pdr"])


def run_study(
    *, ns3_dir: Path, seeds: list[int], steps: int, interval: float,
    initial_distance: float, speed: float,
) -> dict[str, Any]:
    if not seeds or len(seeds) != len(set(seeds)) or any(seed < 1 for seed in seeds):
        raise ValueError("seeds must be distinct positive integers")
    if steps < 2 or interval <= 0 or initial_distance <= 0 or speed <= 0:
        raise ValueError("invalid Wi-Fi study geometry or sampling parameters")
    actual_protocol = {
        "seeds": seeds, "steps": steps, "interval_seconds": interval,
        "initial_distance_meters": initial_distance, "speed_mps": speed,
    }
    if actual_protocol != FROZEN_PROTOCOL:
        raise ValueError(
            "wifi_mobility_packet_v1 is frozen; version the protocol before changing its inputs"
        )
    raw_files: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="flare-wifi-mobility-") as temporary:
        working = Path(temporary)
        binary = working / "flare_wifi_mobility"
        compile_command = _compile(ns3_dir.resolve(), binary)
        ns3_version_path = ns3_dir.resolve() / "VERSION"
        ns3_version = (
            ns3_version_path.read_text(encoding="utf-8").strip()
            if ns3_version_path.is_file() else "unavailable"
        )
        library_dir = ns3_dir.resolve() / "build" / "lib"
        module_hashes = {
            module: hashlib.sha256(_library(library_dir, module).read_bytes()).hexdigest()
            for module in MODULES
        }
        binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
        compiler_version = subprocess.run(
            [compile_command[0], "--version"], capture_output=True,
            text=True, check=True,
        ).stdout.splitlines()[0]
        for seed in seeds:
            for scenario in SCENARIOS:
                path = working / f"{scenario}_seed{seed}.json"
                # Use the same random-stream run within each scenario pair.
                run_number = 1
                command = [
                    str(binary), f"--scenario={scenario}", f"--seed={seed}",
                    f"--run={run_number}", f"--steps={steps}",
                    f"--interval={interval}",
                    f"--initialDistance={initial_distance}", f"--speed={speed}",
                    f"--output={path}",
                ]
                subprocess.run(command, check=True, cwd=ns3_dir)
                raw = path.read_bytes()
                payload = json.loads(raw)
                final_pdr = validate_packet_result(
                    payload, scenario=scenario, seed=seed, steps=steps,
                )
                artifact_name = f"raw/{scenario}_seed{seed}.json"
                raw_files[artifact_name] = raw
                records.append({
                    "seed": seed, "scenario": scenario, "run": run_number,
                    "final_distance_meters": payload["samples"][-1]["distance_meters"],
                    "final_offered_packets": payload["samples"][-1]["cumulative_offered_packets"],
                    "final_received_packets": payload["samples"][-1]["cumulative_received_packets"],
                    "final_pdr": final_pdr,
                    "raw_artifact": artifact_name,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                })
        paired = [
            {
                "seed": seed,
                "stationary_minus_moving_final_pdr": next(
                    record["final_pdr"] for record in records
                    if record["seed"] == seed and record["scenario"] == "stationary"
                ) - next(
                    record["final_pdr"] for record in records
                    if record["seed"] == seed and record["scenario"] == "moving_away"
                ),
            }
            for seed in seeds
        ]
        report = {
            "experiment": "wifi_mobility",
            "protocol_version": "wifi_mobility_packet_v1",
            "evidence_category": "packet_simulation",
            "topology": "two_node_80211b_adhoc_log_distance_v1",
            "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "ns3_version": ns3_version,
            "ns3_module_sha256": module_hashes,
            "compiled_binary_sha256": binary_hash,
            "compiler_version": compiler_version,
            "seeds": seeds,
            "scenarios": list(SCENARIOS),
            "records": records,
            "paired_differences": paired,
            "limitations": [
                "two-node 802.11b ad hoc LogDistance model only",
                "no representative RF captures, attacker, multi-hop routing, or UAV hardware",
                "not integrated into online FL/BiLSTM/DQN control",
            ],
        }
        run = write_experiment_run(
            base=BASE,
            experiment="wifi_mobility",
            protocol_version="wifi_mobility_packet_v1",
            report=report,
            seeds=seeds,
            evidence_category="packet_simulation",
            parameters={
                "steps": steps, "interval_seconds": interval,
                "initial_distance_meters": initial_distance, "speed_mps": speed,
                "source_sha256": report["source_sha256"],
                "ns3_version": ns3_version,
                "ns3_module_sha256": module_hashes,
                "compiled_binary_sha256": binary_hash,
                "compiler_version": compiler_version,
                "compile_command": compile_command,
                "ns3_directory": str(ns3_dir.resolve()),
            },
            extra_files=raw_files,
        )
    return {"report": report, "run_dir": str(run.run_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns3-dir", type=Path, default=BASE / "ns-3-dev")
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 99])
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--initial-distance", type=float, default=15.0)
    parser.add_argument("--speed", type=float, default=12.0)
    args = parser.parse_args()
    try:
        result = run_study(
            ns3_dir=args.ns3_dir, seeds=args.seeds, steps=args.steps,
            interval=args.interval, initial_distance=args.initial_distance,
            speed=args.speed,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"Wi-Fi mobility study failed: {exc}\n")
    print(json.dumps({
        "run_dir": result["run_dir"],
        "evidence_category": "packet_simulation",
        "paired_differences": result["report"]["paired_differences"],
    }, indent=2))


if __name__ == "__main__":
    main()
