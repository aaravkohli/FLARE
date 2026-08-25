"""Deterministic, paired evaluation for learned and baseline routing policies."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np

from rl.checkpoint import validate_checkpoint_metadata
from rl.env import DronePathEnv, SynchronizedTraceDronePathEnv
from rl.reward import REWARD_DEFINITION, compute_routing_reward
from rl.safety import DEFAULT_THREAT_THRESHOLD, constrain_route_action


POLICY_NAMES = (
    "dqn_runtime",
    "dqn_raw",
    "greedy_lowest_threat",
    "myopic_reward",
    "static_direct",
    "random",
)


def _previous_action_from_observation(observation: np.ndarray) -> int | None:
    action_state = observation[9:12]
    if float(action_state.sum()) < 0.5:
        return None
    return int(np.argmax(action_state))


def _choose_action(
    policy_name: str,
    observation: np.ndarray,
    model,
    rng: np.random.Generator,
    threat_threshold: float,
) -> tuple[int, bool]:
    if policy_name in {"dqn_runtime", "dqn_raw"}:
        action, _ = model.predict(observation, deterministic=True)
        action = int(action)
        safety_override = False
        if policy_name == "dqn_runtime":
            constrained = constrain_route_action(
                action,
                observation[:3],
                threat_threshold=threat_threshold,
            )
            action = constrained.action_id
            safety_override = constrained.safety_override
        return action, safety_override
    if policy_name == "greedy_lowest_threat":
        return int(np.argmin(observation[:3])), False
    if policy_name == "myopic_reward":
        previous_action = _previous_action_from_observation(observation)
        rewards = [
            compute_routing_reward(
                action,
                observation[:3],
                observation[3:6],
                observation[6:9],
                previous_action=previous_action,
            ).total
            for action in range(3)
        ]
        return int(np.argmax(rewards)), False
    if policy_name == "static_direct":
        return 0, False
    if policy_name == "random":
        return int(rng.integers(0, 3)), False
    raise ValueError(f"unknown policy {policy_name!r}")


def evaluate_policy_set(
    model,
    *,
    n_episodes: int = 50,
    base_seed: int = 42_000,
    env_factory: Callable[[], DronePathEnv] = DronePathEnv,
    policy_names: Sequence[str] = POLICY_NAMES,
    threat_threshold: float = DEFAULT_THREAT_THRESHOLD,
) -> dict:
    """Evaluate every policy on identical seeded exogenous RF traces.

    ``DronePathEnv`` evolves RF conditions independently of the selected action,
    so resetting every policy with the same per-episode seed gives a paired
    comparison while still retaining route-dependent switching costs.
    """
    if n_episodes < 2:
        raise ValueError("n_episodes must be at least 2 for uncertainty estimates")
    selected_policies = tuple(policy_names)
    if not selected_policies:
        raise ValueError("at least one policy must be selected")
    unknown_policies = set(selected_policies) - set(POLICY_NAMES)
    if unknown_policies:
        raise ValueError(f"unknown policies: {sorted(unknown_policies)}")
    if len(set(selected_policies)) != len(selected_policies):
        raise ValueError("policy_names must not contain duplicates")

    episode_rewards: dict[str, list[float]] = {
        name: [] for name in selected_policies
    }
    results: dict[str, dict] = {}

    for policy_name in selected_policies:
        env = env_factory()
        action_counts = np.zeros(3, dtype=np.int64)
        episode_switches: list[int] = []
        episode_jammed_selections: list[int] = []
        safety_overrides = 0
        no_safe_route_steps = 0
        component_totals = {
            "throughput_contribution": 0.0,
            "delay_penalty": 0.0,
            "energy_penalty": 0.0,
            "loss_penalty": 0.0,
            "switching_penalty": 0.0,
        }
        total_steps = 0

        for episode in range(n_episodes):
            observation, _ = env.reset(seed=base_seed + episode)
            rng = np.random.default_rng(
                base_seed + 1_000_000 + episode
            )
            previous_action: int | None = None
            total_reward = 0.0
            switches = 0
            jammed_selections = 0
            done = False

            while not done:
                action, overridden = _choose_action(
                    policy_name,
                    observation,
                    model,
                    rng,
                    threat_threshold,
                )
                observation, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                total_reward += float(reward)
                action_counts[action] += 1
                switches += int(
                    previous_action is not None and action != previous_action
                )
                jammed_selections += int(bool(info["jammed_paths"][action]))
                safety_overrides += int(overridden)
                no_safe_route_steps += int(
                    not any(
                        float(score) <= threat_threshold
                        for score in info["path_scores"]
                    )
                )
                previous_action = action
                total_steps += 1
                for component in component_totals:
                    component_totals[component] += float(
                        info["reward_components"][component]
                    )

            episode_rewards[policy_name].append(total_reward)
            episode_switches.append(switches)
            episode_jammed_selections.append(jammed_selections)

        env.close()

        rewards = np.asarray(episode_rewards[policy_name], dtype=np.float64)
        reward_std = float(rewards.std(ddof=1))
        results[policy_name] = {
            "mean_episode_reward": float(rewards.mean()),
            "std_episode_reward": reward_std,
            "reward_95ci_half_width": float(
                1.96 * reward_std / math.sqrt(n_episodes)
            ),
            "min_episode_reward": float(rewards.min()),
            "max_episode_reward": float(rewards.max()),
            "mean_switches_per_episode": float(np.mean(episode_switches)),
            "mean_jammed_selections_per_episode": float(
                np.mean(episode_jammed_selections)
            ),
            "safety_override_count": int(safety_overrides),
            "no_safe_route_count": int(no_safe_route_steps),
            "no_safe_route_pct": float(no_safe_route_steps / total_steps * 100.0),
            "action_distribution_pct": {
                name: round(float(action_counts[index] / total_steps * 100.0), 2)
                for index, name in enumerate(("direct", "satellite", "mesh"))
            },
            "mean_reward_components_per_step": {
                name: float(value / total_steps)
                for name, value in component_totals.items()
            },
        }

    if "greedy_lowest_threat" in episode_rewards:
        greedy_rewards = np.asarray(
            episode_rewards["greedy_lowest_threat"], dtype=np.float64
        )
        for policy_name in ("dqn_runtime", "dqn_raw"):
            if policy_name not in episode_rewards:
                continue
            differences = (
                np.asarray(episode_rewards[policy_name], dtype=np.float64)
                - greedy_rewards
            )
            difference_std = float(differences.std(ddof=1))
            results[policy_name]["paired_reward_delta_vs_greedy"] = {
                "mean": float(differences.mean()),
                "95ci_half_width": float(
                    1.96 * difference_std / math.sqrt(n_episodes)
                ),
            }

    protocol_env = env_factory()
    environment_name = type(protocol_env).__name__
    _, protocol_reset_info = protocol_env.reset(seed=base_seed)
    steps_per_episode = int(
        protocol_reset_info.get("episode_steps", protocol_env.max_steps)
    )
    protocol_env.close()

    protocol = {
        "environment": environment_name,
        "reward_definition": REWARD_DEFINITION,
        "paired_seeded_traces": True,
        "base_seed": int(base_seed),
        "n_episodes": int(n_episodes),
        "steps_per_episode": steps_per_episode,
        "safety_threat_threshold": float(threat_threshold),
        "all_unsafe_behavior": "least_risk_route",
    }
    for output_name, info_name in (
        ("scenario", "scenario"),
        ("trace_source", "trace_source"),
        ("trace_generation_seed", "trace_generation_seed"),
        ("trace_fingerprint", "trace_fingerprint"),
    ):
        if info_name in protocol_reset_info:
            protocol[output_name] = protocol_reset_info[info_name]

    return {
        "protocol": protocol,
        "policies": results,
    }


def evaluate_dqn_checkpoint(
    checkpoint_path: str | Path,
    *,
    observation_dim: int,
    action_count: int,
    max_episode_steps: int,
    n_episodes: int = 50,
    base_seed: int = 42_000,
    env_factory: Callable[[], DronePathEnv] = DronePathEnv,
    threat_threshold: float = DEFAULT_THREAT_THRESHOLD,
) -> dict:
    """Validate, load, and evaluate a deployable DQN checkpoint."""
    from stable_baselines3 import DQN

    metadata = validate_checkpoint_metadata(
        checkpoint_path,
        algorithm="dqn",
        observation_dim=observation_dim,
        action_count=action_count,
        max_episode_steps=max_episode_steps,
    )
    model = DQN.load(str(checkpoint_path))
    evaluation = evaluate_policy_set(
        model,
        n_episodes=n_episodes,
        base_seed=base_seed,
        env_factory=env_factory,
        threat_threshold=threat_threshold,
    )
    evaluation["checkpoint_metadata"] = dict(metadata)
    return evaluation


def evaluate_dqn_trace_suite(
    checkpoint_path: str | Path,
    trace_paths: Mapping[str, str | Path],
    *,
    observation_dim: int,
    action_count: int,
    max_episode_steps: int,
    n_episodes: int = 50,
    base_seed: int = 42_000,
    policy_names: Sequence[str] = POLICY_NAMES,
    threat_threshold: float = DEFAULT_THREAT_THRESHOLD,
) -> dict:
    """Evaluate one validated checkpoint across named synchronized trace files."""
    if not trace_paths:
        raise ValueError("trace_paths must contain at least one scenario")

    from stable_baselines3 import DQN

    metadata = validate_checkpoint_metadata(
        checkpoint_path,
        algorithm="dqn",
        observation_dim=observation_dim,
        action_count=action_count,
        max_episode_steps=max_episode_steps,
    )
    model = DQN.load(str(checkpoint_path))
    scenario_results = {}
    for scenario_name, trace_path in trace_paths.items():
        path = Path(trace_path)
        evaluation = evaluate_policy_set(
            model,
            n_episodes=n_episodes,
            base_seed=base_seed,
            env_factory=lambda path=path, scenario_name=scenario_name: (
                SynchronizedTraceDronePathEnv(path, scenario=scenario_name)
            ),
            policy_names=policy_names,
            threat_threshold=threat_threshold,
        )
        recorded_scenario = evaluation["protocol"].get("scenario")
        if recorded_scenario != scenario_name:
            raise ValueError(
                f"trace {path} contains scenario {recorded_scenario!r}, "
                f"expected {scenario_name!r}"
            )
        scenario_results[scenario_name] = evaluation

    return {
        "checkpoint_metadata": dict(metadata),
        "scenarios": scenario_results,
    }
