"""Regression coverage for reproducible RL training/evaluation semantics."""

from __future__ import annotations

import numpy as np

from rl.env import DronePathEnv
from rl.evaluation import POLICY_NAMES, evaluate_policy_set


class _AlwaysDirectModel:
    def predict(self, observation, deterministic=True):
        del observation, deterministic
        return 0, None


def test_initial_observation_explicitly_has_no_previous_action():
    environment = DronePathEnv()

    observation, _ = environment.reset(seed=42)

    assert observation[9:12].tolist() == [0.0, 0.0, 0.0]
    _, _, _, _, info = environment.step(2)
    next_observation = environment._build_obs()
    assert next_observation[9:12].tolist() == [0.0, 0.0, 1.0]
    assert info["reward_components"]["switching_penalty"] == 0.0


def test_policy_evaluation_is_reproducible_and_uses_paired_traces():
    first = evaluate_policy_set(
        _AlwaysDirectModel(),
        n_episodes=3,
        base_seed=900,
    )
    second = evaluate_policy_set(
        _AlwaysDirectModel(),
        n_episodes=3,
        base_seed=900,
    )

    assert first == second
    assert first["protocol"]["paired_seeded_traces"] is True
    assert tuple(first["policies"]) == POLICY_NAMES
    for metrics in first["policies"].values():
        assert np.isfinite(metrics["mean_episode_reward"])


def test_runtime_safety_policy_reduces_jammed_choices_for_unsafe_model():
    result = evaluate_policy_set(
        _AlwaysDirectModel(),
        n_episodes=4,
        base_seed=1_200,
    )["policies"]

    assert result["dqn_runtime"]["safety_override_count"] > 0
    assert (
        result["dqn_runtime"]["mean_jammed_selections_per_episode"]
        < result["dqn_raw"]["mean_jammed_selections_per_episode"]
    )
    assert "paired_reward_delta_vs_greedy" in result["dqn_runtime"]
