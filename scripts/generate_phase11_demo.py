"""Record a controlled RF spot-jamming route sequence from the real runtime.

Run from the repository root:
    venv/bin/python scripts/generate_phase11_demo.py --seed 42

The controller is the in-process mock-SDN FastAPI app. No packet forwarding,
OpenFlow, sensors, or field hardware are involved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(args: list[str]) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True,
    ).strip()


async def _record(seed: int, scratch_dir: Path) -> tuple[list[dict], list[dict], object]:
    import httpx

    import api.server as api_server
    import orchestrator.loop as orchestrator
    import sdn.mock_sdn as mock_sdn
    from simulation.generator import generate_metrics

    orchestrator._CSV_PATH = scratch_dir / "run.csv"
    runtime = orchestrator.Orchestrator()
    if runtime._fl_model is None or "drone_1" not in runtime._rl_agents:
        raise RuntimeError("Both the FL detector and deployed DQN checkpoint are required")
    runtime.DRONES = ["drone_1"]
    runtime._sync_fleet = lambda: None
    runtime._fl_trust_context = lambda: {}
    mock_sdn._flow_table.clear()

    scenarios = [
        {"name": "nominal", "profile": "none", "paths": [], "seed": seed},
        {"name": "spot_direct", "profile": "spot", "paths": ["direct"], "seed": seed + 1},
        {"name": "spot_direct_followup", "profile": "spot", "paths": ["direct"], "seed": seed + 2},
    ]
    generated: list[dict] = []

    async def collect_metrics(_client):
        scenario = scenarios[len(generated)]
        telemetry = generate_metrics(
            "drone_1",
            seed=scenario["seed"],
            jam_state={
                "drone_1": {
                    "profile": scenario["profile"],
                    "paths": scenario["paths"],
                    "gps_drift": 0.0,
                },
            },
        )
        # This capture isolates the RF route story. The generator's synthetic
        # traffic counters are unrelated to spot jamming and are not needed.
        telemetry.pop("security_evidence", None)
        generated.append({"scenario": scenario, "telemetry": telemetry})
        return {"drone_1": telemetry}

    runtime._collect_metrics = collect_metrics
    transport = httpx.ASGITransport(app=mock_sdn.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock-sdn") as client:
        for _ in scenarios:
            await runtime._run_step(client)

    events, has_more = await api_server._mission_events_after(
        0, 100, orchestrator.RUN_ID,
    )
    if has_more:
        raise RuntimeError("Demo journal exceeded the bounded first page")
    return events, generated, runtime


def _assert_sequence(events: list[dict]) -> None:
    completions = [event for event in events if event["phase"] == "completed"]
    requests = [event for event in events if event["phase"] == "requested"]
    if len(completions) != 3:
        raise RuntimeError(f"Expected three completed control cycles; got {len(completions)}")
    expected_paths = ["direct", "satellite", "satellite"]
    actual_paths = [event["event"]["decision"]["installed_path"] for event in completions]
    if actual_paths != expected_paths:
        raise RuntimeError(f"Route sequence was {actual_paths}, expected {expected_paths}")
    if [event["controller"]["status"] for event in completions] != [
        "acknowledged", "acknowledged", "acknowledged",
    ]:
        raise RuntimeError("Mock controller did not acknowledge every route")
    if [event["decision"]["requested_path"] for event in requests] != [
        "direct", "satellite",
    ]:
        raise RuntimeError("Expected real Direct and Satellite pre-apply requests")
    if any(event["event"]["inference"]["source"] != "fl_model" for event in completions):
        raise RuntimeError("FL detector fell back; cannot record model-backed demo")
    if any(event["event"]["decision"]["source"] != "rl_model" for event in completions):
        raise RuntimeError("DQN policy fell back; cannot record model-backed demo")
    if completions[1]["event"]["telemetry"]["ew_status"]["active_attack"] != "spot":
        raise RuntimeError("Step two is not the controlled spot scenario")
    if not (
        completions[1]["event"]["inference"]["path_scores"][0]
        > completions[1]["event"]["inference"]["path_scores"][1]
    ):
        raise RuntimeError("FL detector did not rank degraded Direct above Satellite")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "frontend-react/public/mission-demo/recorded-route-transitions.json",
    )
    parser.add_argument("--probe", action="store_true", help="Print observed decisions without exporting")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="flare-phase11-") as scratch:
        scratch_dir = Path(scratch)
        os.environ["MODE"] = "simulation"
        os.environ["AJ_ENV"] = "development"
        os.environ["DATABASE_PATH"] = str(scratch_dir / "experiment.db")
        events, generated, runtime = asyncio.run(_record(args.seed, scratch_dir))
        summary = [
            {
                "cursor": event["cursor"],
                "phase": event["phase"],
                "step": event["step"],
                "path": (
                    event["decision"]["requested_path"]
                    if event["phase"] == "requested"
                    else event["event"]["decision"]["installed_path"]
                ),
                "policy": (
                    event["decision"]["policy_path"]
                    if event["phase"] == "requested"
                    else event["event"]["decision"]["policy_path"]
                ),
                "scores": (
                    event["inference"]["path_scores"]
                    if event["phase"] == "requested"
                    else event["event"]["inference"]["path_scores"]
                ),
            }
            for event in events
        ]
        if args.probe:
            print(json.dumps(summary, indent=2))
            return
        _assert_sequence(events)

        command = (
            "venv/bin/python scripts/generate_phase11_demo.py "
            f"--seed {args.seed} --output {args.output}"
        )
        config_paths = [
            "config/mode.yaml", "config/fl_config.yaml", "config/rl_config.yaml",
            "config/sdn_config.yaml", "config/security_config.yaml",
            "config/fleet_registry.yaml",
        ]
        source_paths = [
            "orchestrator/loop.py", "schemas/decision_event.py", "api/server.py",
            "scripts/generate_phase11_demo.py", "simulation/generator.py",
            "sdn/mock_sdn.py",
        ]
        git_status = _git(["status", "--porcelain=v1"])
        payload = {
            "schema_version": "recorded_route_transitions_v1",
            "provenance": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "command": command,
                "git_revision": _git(["rev-parse", "HEAD"]),
                "git_worktree_dirty": bool(git_status),
                "git_status_sha256": hashlib.sha256(git_status.encode()).hexdigest(),
                "seed_list": [args.seed, args.seed + 1, args.seed + 2],
                "configuration_sha256": {
                    path: _sha256(ROOT / path) for path in config_paths
                },
                "source_sha256": {
                    path: _sha256(ROOT / path) for path in source_paths
                },
                "checkpoint_sha256": {
                    "fl_detector": runtime._fl_model_id,
                    "routing_dqn": runtime._rl_model_id,
                },
                "dataset_fingerprint_sha256": hashlib.sha256(
                    json.dumps(
                        [
                            event["event"]["telemetry"]
                            for event in events if event["phase"] == "completed"
                        ],
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "runtime": "simulation",
                "controller": "in-process mock_sdn FastAPI ASGI transport",
                "evidence_category": "controlled_simulation_mock_sdn",
            },
            "fleet": ["drone_1"],
            "run_id": events[0]["run_id"],
            "scenario_steps": [item["scenario"] for item in generated],
            "events": events,
            "source_limitations": [
                "This recorded route sequence follows one UAV, drone_1; it is not a fleet replay.",
                "RF telemetry and spot jamming are synthetic controlled simulation.",
                "The spot label is scenario ground truth; the FL detector consumes RF features, not that label.",
                "Mock-SDN acknowledgements prove only mock controller state, not packet forwarding or OpenFlow enforcement.",
                "Synthetic traffic counters were omitted; network security and insider analysis are unavailable in this route capture.",
                "The FL trust context is fixed to its default neutral value to isolate the RF route decision.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
