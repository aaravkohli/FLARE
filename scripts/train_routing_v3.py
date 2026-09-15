"""Train and evaluate FLARE's 18-input, four-action trust-aware DQN."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from stable_baselines3 import DQN

from rl.checkpoint import checkpoint_metadata_path, write_checkpoint_metadata
from rl.env import TrustAwareDronePathEnv
from runtime.model_deployment import publish_checkpoint
from schemas.contracts import ROUTING_STATE_V3
from provenance import promote_run, write_experiment_run


BASE = Path(__file__).parent.parent


def evaluate(model: DQN, *, episodes: int, seed: int) -> dict:
    env = TrustAwareDronePathEnv()
    rewards = []
    unsafe_opportunities = unsafe_forwarded = raw_unsafe_forwarded = 0
    clean_steps = clean_forwarded = 0
    holds = justified_holds = 0
    for episode in range(episodes):
        obs, _ = env.reset(seed=seed + episode)
        done = False
        total = 0.0
        while not done:
            risk = max(float(obs[-3]), 1.0 - float(obs[-4]), float(obs[-2]))
            raw_action, _ = model.predict(obs, deterministic=True)
            raw_action = int(raw_action)
            action = raw_action
            safe_routes = [index for index, score in enumerate(obs[:3]) if score <= 0.8]
            if risk >= 0.72 or not safe_routes:
                action = 3
            elif raw_action == 3:
                action = 3
            elif raw_action not in safe_routes:
                action = min(safe_routes, key=lambda index: float(obs[index]))
            if risk >= 0.72:
                unsafe_opportunities += 1
                raw_unsafe_forwarded += int(raw_action != 3)
                unsafe_forwarded += int(action != 3)
            if risk < 0.35:
                clean_steps += 1
                clean_forwarded += int(action != 3)
            if action == 3:
                holds += 1
                justified_holds += int(risk >= 0.72 or min(obs[:3]) > 0.8)
            obs, reward, terminated, truncated, _ = env.step(action)
            total += reward
            done = terminated or truncated
        rewards.append(total)
    return {
        "episodes": episodes,
        "mean_episode_reward": float(np.mean(rewards)),
        "std_episode_reward": float(np.std(rewards)),
        "unsafe_forward_rate": unsafe_forwarded / max(unsafe_opportunities, 1),
        "raw_policy_unsafe_forward_rate": raw_unsafe_forwarded / max(unsafe_opportunities, 1),
        "clean_forward_availability": clean_forwarded / max(clean_steps, 1),
        "hold_precision": justified_holds / max(holds, 1),
        "hold_actions": holds,
    }


def train(
    *, timesteps: int, seed: int, output: Path, episodes: int,
    published_report: Path | None = None, promote: bool = False,
    deploy: bool = False,
) -> dict:
    env = TrustAwareDronePathEnv()
    env.reset(seed=seed)
    model = DQN(
        "MlpPolicy",
        env,
        learning_rate=1e-4,
        buffer_size=20_000,
        batch_size=64,
        exploration_fraction=0.25,
        exploration_final_eps=0.05,
        target_update_interval=500,
        policy_kwargs={"net_arch": [128, 128]},
        seed=seed,
        verbose=0,
    )
    model.learn(total_timesteps=timesteps)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(output))
    write_checkpoint_metadata(
        output,
        algorithm="dqn",
        observation_dim=18,
        action_count=4,
        max_episode_steps=env.max_steps,
        training_timesteps=timesteps,
        training_mode="synthetic_cross_layer_security",
        training_seed=seed,
        observation_definition=ROUTING_STATE_V3,
        training_provenance={
            "security_inputs": [
                "client_trust",
                "insider_risk",
                "containment_score",
                "evidence_freshness",
            ],
            "actions": ["direct", "satellite", "mesh", "hold"],
        },
    )
    metrics = evaluate(model, episodes=episodes, seed=seed + 10_000)
    report = {
        "routing_contract": ROUTING_STATE_V3,
        "training_timesteps": timesteps,
        "training_seed": seed,
        "checkpoint": str(output),
        "evaluation": metrics,
    }
    run_artifact = write_experiment_run(
        base=BASE,
        experiment="routing_v3_training",
        protocol_version="routing_state_v3_dqn_training_v1",
        report=report,
        seeds=[seed],
        evidence_category="controlled_simulation",
        config_paths=[Path("config/rl_config.yaml")],
        checkpoint_paths=[output, checkpoint_metadata_path(output)],
        parameters={"timesteps": timesteps, "evaluation_episodes": episodes},
    )
    report["immutable_run_dir"] = str(run_artifact.run_dir)
    if promote:
        promote_run(
            base=BASE,
            run_dir=run_artifact.run_dir,
            published_path=published_report or BASE / "results" / "routing_v3_evaluation.json",
            expected_experiment="routing_v3_training",
            expected_protocol="routing_state_v3_dqn_training_v1",
            required_seeds=[seed],
        )
    if deploy:
        deployment = publish_checkpoint(
            base=BASE,
            manifest_path=BASE / "models" / "deployment_manifest.json",
            model_name="routing",
            checkpoint_path=output,
            metadata_path=checkpoint_metadata_path(output),
            contract=ROUTING_STATE_V3,
            provenance_run_id=run_artifact.run_dir.name,
        )
        report["deployed_generation"] = deployment["generation"]
    env.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument(
        "--output", type=Path, default=BASE / "models" / "routing_v3_candidate.zip"
    )
    parser.add_argument(
        "--report", type=Path, default=BASE / "results" / "routing_v3_evaluation.json"
    )
    parser.add_argument("--promote", action="store_true")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Atomically publish the candidate through deployment_manifest.json",
    )
    args = parser.parse_args()
    if args.timesteps < 1 or args.episodes < 1:
        parser.error("timesteps and episodes must be positive")
    print(json.dumps(train(
        timesteps=args.timesteps,
        seed=args.seed,
        output=args.output,
        episodes=args.episodes,
        published_report=args.report,
        promote=args.promote,
        deploy=args.deploy,
    ), indent=2))


if __name__ == "__main__":
    main()
