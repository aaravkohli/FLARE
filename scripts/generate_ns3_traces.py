#!/usr/bin/env python3
"""Run FLARE's packet-level ns-3 simulation and build an RL replay trace."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from rl.ns3_trace import convert_ns3_packet_trace_files  # noqa: E402
from rl.traces import trace_fingerprint, write_synchronized_trace  # noqa: E402


NS3_SCENARIOS = ("clean", "iid", "persistent_spot", "barrage", "reactive")
CANONICAL_SOURCE = BASE / "simulation" / "ns3" / "flare_packet_trace.cc"


def _find_ns3_library(library_directory: Path, module: str) -> Path:
    candidates = sorted(
        path
        for path in library_directory.glob(f"libns3-*-{module}-*")
        if path.is_file() and (".so" in path.name or path.suffix == ".dylib")
    )
    if not candidates:
        raise FileNotFoundError(
            f"configured ns-3 library for module {module!r} not found in "
            f"{library_directory}; run './ns3 configure' and './ns3 build' first"
        )
    # Module names can prefix other modules (for example ``internet`` and
    # ``internet-apps``). The base module has the shortest matching filename.
    return min(candidates, key=lambda path: (len(path.name), path.name))


def _build_packet_trace_binary(ns3_directory: Path, binary_path: Path) -> Path:
    if not CANONICAL_SOURCE.is_file():
        raise FileNotFoundError(f"canonical ns-3 source not found: {CANONICAL_SOURCE}")
    include_directory = ns3_directory / "build" / "include"
    library_directory = ns3_directory / "build" / "lib"
    if not include_directory.is_dir() or not library_directory.is_dir():
        raise FileNotFoundError(
            f"configured ns-3 build not found under {ns3_directory / 'build'}; "
            "run './ns3 configure' and './ns3 build' first"
        )

    modules = (
        "applications",
        "point-to-point",
        "internet",
        "traffic-control",
        "network",
        "stats",
        "core",
    )
    libraries = [_find_ns3_library(library_directory, module) for module in modules]
    dependencies = [CANONICAL_SOURCE, *libraries]
    needs_build = not binary_path.is_file() or any(
        dependency.stat().st_mtime_ns > binary_path.stat().st_mtime_ns
        for dependency in dependencies
    )
    if not needs_build:
        return binary_path

    compiler_value = os.environ.get("CXX") or shutil.which("c++")
    if not compiler_value:
        raise FileNotFoundError("a C++ compiler was not found; set CXX explicitly")
    compiler = shutil.which(compiler_value) or compiler_value
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        compiler,
        "-std=c++20",
        "-O2",
        f"-I{include_directory}",
        str(CANONICAL_SOURCE),
        "-o",
        str(binary_path),
        *(str(library) for library in libraries),
        f"-Wl,-rpath,{library_directory}",
    ]
    subprocess.run(command, check=True)
    return binary_path


def generate_packet_traces(
    *,
    ns3_directory: Path,
    scenarios: list[str],
    episodes_per_scenario: int,
    steps: int,
    interval_seconds: float,
    seed: int,
    raw_directory: Path,
) -> list[Path]:
    """Build/run ns-3 and return deterministic raw episode paths."""
    if not (ns3_directory / "ns3").is_file():
        raise FileNotFoundError(
            f"ns-3 source tree not found: {ns3_directory}"
        )
    raw_directory.mkdir(parents=True, exist_ok=True)
    binary = _build_packet_trace_binary(
        ns3_directory,
        raw_directory / ".tools" / "flare_packet_trace",
    )

    outputs: list[Path] = []
    run_number = 1
    for scenario in scenarios:
        for episode_index in range(episodes_per_scenario):
            episode_id = (
                f"{scenario}:ns3:seed={seed}:run={run_number:05d}"
            )
            destination = raw_directory / (
                f"{scenario}_seed{seed}_run{run_number:05d}.json"
            )
            temporary_fd, temporary_name = tempfile.mkstemp(
                dir=raw_directory,
                prefix=f".{destination.stem}.",
                suffix=".tmp.json",
            )
            os.close(temporary_fd)
            temporary_path = Path(temporary_name)
            command = [
                str(binary),
                f"--scenario={scenario}",
                f"--steps={steps}",
                f"--interval={interval_seconds}",
                f"--seed={seed}",
                f"--run={run_number}",
                f"--episodeId={episode_id}",
                f"--output={temporary_path}",
            ]
            try:
                subprocess.run(command, cwd=ns3_directory, check=True)
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
            outputs.append(destination)
            print(
                f"Generated {scenario} packet episode {episode_index + 1}/"
                f"{episodes_per_scenario}: {destination}"
            )
            run_number += 1
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate timestamp-aligned packet-level ns-3 episodes and convert "
            "them to FLARE's synchronized RL trace format"
        )
    )
    parser.add_argument(
        "--ns3-dir",
        type=Path,
        default=BASE / "ns-3-dev",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=NS3_SCENARIOS,
        default=["persistent_spot", "barrage", "reactive"],
    )
    parser.add_argument("--episodes-per-scenario", type=int, default=3)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=60_000)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=BASE / "datasets" / "processed" / "ns3_raw",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE / "datasets" / "processed" / "rl_trace_ns3.csv",
    )
    args = parser.parse_args()

    if args.episodes_per_scenario < 1:
        parser.error("--episodes-per-scenario must be at least 1")
    if args.steps < 1:
        parser.error("--steps must be at least 1")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.seed < 1:
        parser.error("--seed must be at least 1")

    raw_paths = generate_packet_traces(
        ns3_directory=args.ns3_dir.resolve(),
        scenarios=list(dict.fromkeys(args.scenarios)),
        episodes_per_scenario=args.episodes_per_scenario,
        steps=args.steps,
        interval_seconds=args.interval,
        seed=args.seed,
        raw_directory=args.raw_dir.resolve(),
    )
    frame = convert_ns3_packet_trace_files(raw_paths)
    output = write_synchronized_trace(frame, args.output.resolve())
    print(
        f"Wrote {len(frame)} synchronized packet-derived rows across "
        f"{frame['episode_id'].nunique()} episodes to {output} "
        f"[sha256:{trace_fingerprint(frame)[:12]}]"
    )


if __name__ == "__main__":
    main()
