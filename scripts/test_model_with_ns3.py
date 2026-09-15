#!/usr/bin/env python3
"""Evaluate the deployed DQN on validated packet-derived ns-3 traces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from rl.evaluation import evaluate_dqn_trace_suite  # noqa: E402
from rl.traces import (  # noqa: E402
    load_synchronized_trace,
    trace_fingerprint,
)
from provenance import promote_run, write_experiment_run  # noqa: E402


RL_CONFIG = yaml.safe_load((BASE / "config" / "rl_config.yaml").read_text())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate FLARE routing policies on packet-level ns-3 traces"
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=BASE / RL_CONFIG["paths"]["ns3_trace_csv"],
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=BASE / RL_CONFIG["paths"]["model_save"],
    )
    parser.add_argument(
        "--episodes",
        type=int,
        help="episodes per scenario; defaults to every available episode",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=BASE / "results" / "ns3_evaluation_report.json",
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    frame = load_synchronized_trace(args.trace)
    if not frame["source"].astype(str).str.startswith("ns3:packet-level:").all():
        parser.error("--trace must contain only packet-derived ns-3 episodes")

    counts = frame.groupby("scenario")["episode_id"].nunique().to_dict()
    if any(count < 2 for count in counts.values()):
        parser.error(
            "every scenario needs at least two episodes for confidence intervals; "
            f"found {counts}"
        )
    episode_count = args.episodes or min(counts.values())
    if episode_count < 2:
        parser.error("--episodes must be at least 2")
    if any(count < episode_count for count in counts.values()):
        parser.error(
            f"--episodes={episode_count} exceeds available scenario counts {counts}"
        )

    environment = RL_CONFIG["environment"]
    scenario_paths = {
        str(scenario): args.trace for scenario in sorted(counts)
    }
    evaluation = evaluate_dqn_trace_suite(
        args.checkpoint,
        scenario_paths,
        observation_dim=int(environment["obs_dim"]),
        action_count=int(environment["num_paths"]),
        max_episode_steps=int(environment["max_steps"]),
        n_episodes=episode_count,
        base_seed=0,
        threat_threshold=float(
            RL_CONFIG.get("safety", {}).get("threat_threshold", 0.8)
        ),
    )

    report = {
        "protocol": {
            "trace_path": str(args.trace.resolve()),
            "trace_sha256": trace_fingerprint(frame),
            "scenario_episode_counts": counts,
            "evaluated_episodes_per_scenario": episode_count,
            "threat_source": "qos-risk proxy, not FL inference",
        },
        **evaluation,
    }
    run_artifact = write_experiment_run(
        base=BASE,
        experiment="ns3_routing_evaluation",
        protocol_version="ns3_packet_trace_evaluation_v1",
        report=report,
        seeds=[],
        evidence_category="packet_simulation",
        config_paths=[Path("config/rl_config.yaml")],
        checkpoint_paths=[
            args.checkpoint,
            args.checkpoint.with_name(f"{args.checkpoint.name}.metadata.json"),
        ],
        dataset_paths=[args.trace],
        dataset_roles={str(args.trace): "packet_simulation_evaluation"},
        dataset_fingerprints={str(args.trace): trace_fingerprint(frame)},
        parameters={"episodes_per_scenario": episode_count},
    )
    if args.promote:
        promote_run(
            base=BASE,
            run_dir=run_artifact.run_dir,
            published_path=args.report,
            expected_experiment="ns3_routing_evaluation",
            expected_protocol="ns3_packet_trace_evaluation_v1",
            required_seeds=[],
        )

    print("\nPacket-level ns-3 routing evaluation")
    for scenario, result in evaluation["scenarios"].items():
        runtime = result["policies"]["dqn_runtime"]
        delta = runtime["paired_reward_delta_vs_greedy"]
        print(
            f"  {scenario:<16} reward={runtime['mean_episode_reward']:.3f} "
            f"CI±{runtime['reward_95ci_half_width']:.3f} "
            f"delta-vs-greedy={delta['mean']:+.3f}±{delta['95ci_half_width']:.3f}"
        )
    print(f"Report: {run_artifact.report_path}")


if __name__ == "__main__":
    main()
