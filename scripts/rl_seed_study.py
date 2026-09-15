"""Train and compare DQN routing policies across independent random seeds.

The study uses disjoint synchronized traces for training/model selection, then
evaluates every validation-best checkpoint on the same four held-out scenario
files.  This separates training-seed variance from environment-trace variance.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import yaml


_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RL-SEEDS] %(message)s")
logger = logging.getLogger(__name__)

from rl.evaluation import evaluate_dqn_trace_suite
from rl.traces import (
    ROBUSTNESS_SCENARIOS,
    generate_synchronized_trace,
    validate_disjoint_trace_files,
    write_synchronized_trace,
)
from rl.train import train_dqn
from provenance import promote_run, write_experiment_run

_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_ENV_CFG = _RL_CFG["environment"]
_PATHS_CFG = _RL_CFG["paths"]
_TRAIN_CFG = _RL_CFG["training"]


def _scenario_trace_paths(
    *,
    n_episodes: int,
    generation_seed: int,
) -> dict[str, Path]:
    trace_dir = _BASE / "datasets" / "processed" / "rl_seed_study_traces"
    paths = {}
    for scenario_index, scenario in enumerate(ROBUSTNESS_SCENARIOS):
        frame = generate_synchronized_trace(
            scenario=scenario,
            n_episodes=n_episodes,
            steps_per_episode=int(_ENV_CFG["max_steps"]),
            base_seed=generation_seed + scenario_index * 10_000,
        )
        paths[scenario] = write_synchronized_trace(
            frame,
            trace_dir / f"{scenario}.csv",
        )
    return paths


def _aggregate_seed_results(per_seed: dict[str, dict]) -> dict:
    aggregated = {}
    for scenario in ROBUSTNESS_SCENARIOS:
        rewards = np.asarray([
            result["scenarios"][scenario]["policies"]["dqn_runtime"][
                "mean_episode_reward"
            ]
            for result in per_seed.values()
        ], dtype=np.float64)
        deltas = np.asarray([
            result["scenarios"][scenario]["policies"]["dqn_runtime"][
                "paired_reward_delta_vs_greedy"
            ]["mean"]
            for result in per_seed.values()
        ], dtype=np.float64)
        jammed = np.asarray([
            result["scenarios"][scenario]["policies"]["dqn_runtime"][
                "mean_jammed_selections_per_episode"
            ]
            for result in per_seed.values()
        ], dtype=np.float64)
        aggregated[scenario] = {
            "mean_reward_across_training_seeds": float(rewards.mean()),
            "std_reward_across_training_seeds": float(
                rewards.std(ddof=1) if len(rewards) > 1 else 0.0
            ),
            "min_seed_reward": float(rewards.min()),
            "max_seed_reward": float(rewards.max()),
            "mean_paired_delta_vs_greedy": float(deltas.mean()),
            "std_paired_delta_vs_greedy": float(
                deltas.std(ddof=1) if len(deltas) > 1 else 0.0
            ),
            "mean_jammed_selections_per_episode": float(jammed.mean()),
        }
    return aggregated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a synchronized-trace DQN training-seed study"
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 99])
    parser.add_argument(
        "--timesteps",
        type=int,
        default=int(_TRAIN_CFG["total_timesteps"]),
    )
    parser.add_argument("--validation-episodes", type=int, default=20)
    parser.add_argument("--validation-frequency", type=int, default=10_000)
    parser.add_argument(
        "--validation-seed",
        type=int,
        default=int(_TRAIN_CFG.get("evaluation_seed", 42_000)),
    )
    parser.add_argument("--evaluation-episodes", type=int, default=50)
    parser.add_argument("--evaluation-trace-seed", type=int, default=52_000)
    parser.add_argument(
        "--training-trace",
        type=Path,
        default=_BASE / _PATHS_CFG["synchronized_trace_csv"],
    )
    parser.add_argument(
        "--validation-trace",
        type=Path,
        default=_BASE / _PATHS_CFG["synchronized_trace_eval_csv"],
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_BASE / "models" / "rl_seed_study",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=_BASE / "results" / "rl_seed_study.json",
    )
    parser.add_argument(
        "--skip-training",
        action="store_true",
        help="Evaluate existing seed checkpoints without retraining",
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    if len(set(args.seeds)) != len(args.seeds):
        parser.error("--seeds must not contain duplicates")
    if args.timesteps < 1:
        parser.error("--timesteps must be at least 1")
    if args.validation_episodes < 1 or args.evaluation_episodes < 2:
        parser.error("validation episodes must be >=1 and evaluation episodes >=2")
    if args.validation_frequency < 1:
        parser.error("--validation-frequency must be at least 1")

    provenance = validate_disjoint_trace_files(
        args.training_trace,
        args.validation_trace,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenario_paths = _scenario_trace_paths(
        n_episodes=args.evaluation_episodes,
        generation_seed=args.evaluation_trace_seed,
    )

    per_seed = {}
    checkpoint_paths = {}
    for seed in args.seeds:
        final_path = args.output_dir / f"seed_{seed}_final.zip"
        best_path = final_path.with_name(f"{final_path.stem}_best.zip")
        if not args.skip_training:
            logger.info("Training trace DQN with seed %d", seed)
            train_dqn(
                total_timesteps=args.timesteps,
                use_trace_data=True,
                tb_log=None,
                seed=seed,
                output_path=final_path,
                trace_csv=args.training_trace,
                trace_eval_csv=args.validation_trace,
                evaluation_episodes=args.validation_episodes,
                evaluation_frequency=args.validation_frequency,
                evaluation_seed=args.validation_seed,
            )
        checkpoint_path = best_path if best_path.is_file() else final_path
        if not checkpoint_path.is_file():
            raise FileNotFoundError(
                f"seed {seed} checkpoint not found at {best_path} or {final_path}"
            )
        logger.info("Evaluating seed %d checkpoint %s", seed, checkpoint_path.name)
        checkpoint_paths[str(seed)] = str(checkpoint_path)
        per_seed[str(seed)] = evaluate_dqn_trace_suite(
            checkpoint_path,
            scenario_paths,
            observation_dim=int(_ENV_CFG["obs_dim"]),
            action_count=int(_ENV_CFG["num_paths"]),
            max_episode_steps=int(_ENV_CFG["max_steps"]),
            n_episodes=args.evaluation_episodes,
            base_seed=0,
            policy_names=(
                "dqn_runtime",
                "dqn_raw",
                "greedy_lowest_threat",
            ),
            threat_threshold=float(
                _RL_CFG.get("safety", {}).get("threat_threshold", 0.8)
            ),
        )

    report = {
        "protocol": {
            "training_seeds": args.seeds,
            "training_timesteps": args.timesteps,
            "validation_episodes": args.validation_episodes,
            "validation_frequency": args.validation_frequency,
            "validation_seed": args.validation_seed,
            "evaluation_episodes": args.evaluation_episodes,
            "evaluation_trace_seed": args.evaluation_trace_seed,
            "training_trace_provenance": provenance,
            "checkpoint_paths": checkpoint_paths,
        },
        "aggregate_across_training_seeds": _aggregate_seed_results(per_seed),
        "per_seed": per_seed,
    }
    checkpoint_inputs = []
    for checkpoint in checkpoint_paths.values():
        path = Path(checkpoint)
        checkpoint_inputs.extend([path, path.with_name(f"{path.name}.metadata.json")])
    run_artifact = write_experiment_run(
        base=_BASE,
        experiment="rl_seed_study",
        protocol_version="synchronized_trace_dqn_seed_study_v1",
        report=report,
        seeds=args.seeds,
        evidence_category="controlled_simulation",
        config_paths=[Path("config/rl_config.yaml")],
        checkpoint_paths=checkpoint_inputs,
        dataset_paths=[args.training_trace, args.validation_trace, *scenario_paths.values()],
        dataset_roles={
            str(args.training_trace): "training",
            str(args.validation_trace): "validation",
            **{str(path): f"held_out_{name}" for name, path in scenario_paths.items()},
        },
        parameters={
            "timesteps": args.timesteps,
            "validation_episodes": args.validation_episodes,
            "evaluation_episodes": args.evaluation_episodes,
        },
    )
    if args.promote:
        promote_run(
            base=_BASE,
            run_dir=run_artifact.run_dir,
            published_path=args.report,
            expected_experiment="rl_seed_study",
            expected_protocol="synchronized_trace_dqn_seed_study_v1",
        )

    print("\nTraining-seed robustness summary")
    for scenario, metrics in report["aggregate_across_training_seeds"].items():
        print(
            f"  {scenario:<16} reward "
            f"{metrics['mean_reward_across_training_seeds']:.3f} ± "
            f"{metrics['std_reward_across_training_seeds']:.3f} across seeds; "
            f"Δgreedy {metrics['mean_paired_delta_vs_greedy']:+.3f}"
        )
    print(f"Report: {run_artifact.report_path}")


if __name__ == "__main__":
    main()
