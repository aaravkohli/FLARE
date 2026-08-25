"""Regression coverage for the shared training/runtime routing reward."""

from __future__ import annotations

import json

import numpy as np
import pytest
from pydantic import ValidationError

from orchestrator.loop import Orchestrator
from rl.checkpoint import (
    CheckpointCompatibilityError,
    checkpoint_metadata_path,
    validate_checkpoint_metadata,
    write_checkpoint_metadata,
)
from rl.env import DronePathEnv
from rl.reward import (
    DEFAULT_REWARD_WEIGHTS,
    REWARD_DEFINITION,
    compute_routing_reward,
)
from schemas.decision_event import OutcomeTrace


PATH_SCORES = [0.1, 0.2, 0.8]
PATH_LATENCIES = [0.2, 0.4, 0.9]
PATH_LOSSES = [0.02, 0.05, 0.7]


def test_shared_reward_matches_documented_weighted_formula():
    result = compute_routing_reward(
        1,
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
        previous_action=0,
    )
    expected = (
        DEFAULT_REWARD_WEIGHTS.throughput * (1.0 - PATH_SCORES[1])
        - DEFAULT_REWARD_WEIGHTS.delay * PATH_LATENCIES[1]
        - DEFAULT_REWARD_WEIGHTS.energy * 0.6
        - DEFAULT_REWARD_WEIGHTS.loss * PATH_LOSSES[1]
        - DEFAULT_REWARD_WEIGHTS.switching
    )

    assert result.total == pytest.approx(expected)
    assert result.switched is True
    assert result.switching_penalty == pytest.approx(
        DEFAULT_REWARD_WEIGHTS.switching
    )


def test_first_decision_has_no_switching_penalty():
    first = compute_routing_reward(
        2,
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
        previous_action=None,
    )
    switched = compute_routing_reward(
        2,
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
        previous_action=0,
    )

    assert first.switched is False
    assert first.total - switched.total == pytest.approx(
        DEFAULT_REWARD_WEIGHTS.switching
    )


def test_training_environment_uses_shared_reward_implementation():
    env = DronePathEnv()
    env._path_scores = np.array(PATH_SCORES, dtype=np.float32)
    env._path_latencies = np.array(PATH_LATENCIES, dtype=np.float32)
    env._path_losses = np.array(PATH_LOSSES, dtype=np.float32)
    env._prev_action = 0
    env._step = 4

    environment_reward = env._reward_breakdown(1)
    shared_reward = compute_routing_reward(
        1,
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
        previous_action=0,
    )

    assert environment_reward.total == pytest.approx(shared_reward.total)
    assert environment_reward.as_dict() == pytest.approx(shared_reward.as_dict())


def test_environment_scores_the_state_seen_by_the_policy_before_transition():
    env = DronePathEnv()
    env._path_scores = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    env._path_latencies = np.array([0.2, 0.3, 0.4], dtype=np.float32)
    env._path_losses = np.array([0.01, 0.02, 0.03], dtype=np.float32)
    env._prev_action = 0
    env._step = 0
    expected = compute_routing_reward(
        0,
        env._path_scores,
        env._path_latencies,
        env._path_losses,
    )

    def evolve_to_unseen_state():
        env._path_scores[:] = 1.0
        env._path_latencies[:] = 1.0
        env._path_losses[:] = 1.0

    env._evolve_rf_state = evolve_to_unseen_state
    observation, reward, *_rest, info = env.step(0)

    assert reward == pytest.approx(expected.total)
    assert info["path_scores"] == pytest.approx([0.1, 0.2, 0.3])
    assert info["next_path_scores"] == [1.0, 1.0, 1.0]
    assert observation[:3].tolist() == [1.0, 1.0, 1.0]


def test_orchestrator_uses_shared_reward_implementation():
    runtime = Orchestrator.__new__(Orchestrator)

    runtime_reward = runtime._compute_reward(
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
        1,
        0,
    )
    shared_reward = compute_routing_reward(
        1,
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
        previous_action=0,
    )

    assert runtime_reward == shared_reward


def test_canonical_outcome_rejects_a_reward_component_mismatch():
    breakdown = compute_routing_reward(
        0,
        PATH_SCORES,
        PATH_LATENCIES,
        PATH_LOSSES,
    )

    with pytest.raises(ValidationError, match="reward must equal"):
        OutcomeTrace.model_validate({
            "reward": breakdown.total + 0.5,
            "reward_definition": REWARD_DEFINITION,
            "reward_components": breakdown.as_dict(),
            "path": "direct",
            "estimated": False,
            "packet_loss": PATH_LOSSES[0],
        })


def test_checkpoint_metadata_round_trip_accepts_current_reward(tmp_path):
    checkpoint = tmp_path / "policy.zip"
    checkpoint.write_bytes(b"current-policy")

    sidecar = write_checkpoint_metadata(
        checkpoint,
        algorithm="dqn",
        observation_dim=14,
        action_count=3,
        max_episode_steps=500,
        training_timesteps=100,
        training_mode="synthetic",
        training_seed=42,
    )
    metadata = validate_checkpoint_metadata(
        checkpoint,
        algorithm="dqn",
        observation_dim=14,
        action_count=3,
        max_episode_steps=500,
    )

    assert sidecar == checkpoint_metadata_path(checkpoint)
    assert metadata["reward_definition"] == REWARD_DEFINITION
    assert metadata["training_timesteps"] == 100
    assert metadata["training_seed"] == 42
    assert metadata["observation_definition"] == "routing_state_v2"


def test_checkpoint_without_metadata_is_rejected(tmp_path):
    checkpoint = tmp_path / "legacy-policy.zip"
    checkpoint.write_bytes(b"legacy-policy")

    with pytest.raises(CheckpointCompatibilityError, match="metadata not found"):
        validate_checkpoint_metadata(
            checkpoint,
            algorithm="dqn",
            observation_dim=14,
            action_count=3,
            max_episode_steps=500,
        )


def test_checkpoint_with_old_reward_definition_is_rejected(tmp_path):
    checkpoint = tmp_path / "old-reward-policy.zip"
    checkpoint.write_bytes(b"old-reward-policy")
    sidecar = write_checkpoint_metadata(
        checkpoint,
        algorithm="dqn",
        observation_dim=14,
        action_count=3,
        max_episode_steps=500,
        training_timesteps=100,
        training_mode="synthetic",
        training_seed=42,
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["reward_definition"] = "routing_qos_v1"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(CheckpointCompatibilityError, match="reward_definition"):
        validate_checkpoint_metadata(
            checkpoint,
            algorithm="dqn",
            observation_dim=14,
            action_count=3,
            max_episode_steps=500,
        )


def test_checkpoint_hash_mismatch_is_rejected(tmp_path):
    checkpoint = tmp_path / "replaced-policy.zip"
    checkpoint.write_bytes(b"original-policy")
    write_checkpoint_metadata(
        checkpoint,
        algorithm="dqn",
        observation_dim=14,
        action_count=3,
        max_episode_steps=500,
        training_timesteps=100,
        training_mode="synthetic",
        training_seed=42,
    )
    checkpoint.write_bytes(b"different-policy")

    with pytest.raises(CheckpointCompatibilityError, match="SHA-256"):
        validate_checkpoint_metadata(
            checkpoint,
            algorithm="dqn",
            observation_dim=14,
            action_count=3,
            max_episode_steps=500,
        )


def test_checkpoint_with_different_reward_contract_is_rejected(tmp_path):
    checkpoint = tmp_path / "different-reward-contract.zip"
    checkpoint.write_bytes(b"policy")
    sidecar = write_checkpoint_metadata(
        checkpoint,
        algorithm="dqn",
        observation_dim=14,
        action_count=3,
        max_episode_steps=500,
        training_timesteps=100,
        training_mode="synthetic",
        training_seed=42,
    )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["reward_contract_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(CheckpointCompatibilityError, match="reward_contract_sha256"):
        validate_checkpoint_metadata(
            checkpoint,
            algorithm="dqn",
            observation_dim=14,
            action_count=3,
            max_episode_steps=500,
        )


def test_checkpoint_with_different_episode_length_is_rejected(tmp_path):
    checkpoint = tmp_path / "different-episode-length.zip"
    checkpoint.write_bytes(b"policy")
    write_checkpoint_metadata(
        checkpoint,
        algorithm="dqn",
        observation_dim=14,
        action_count=3,
        max_episode_steps=500,
        training_timesteps=100,
        training_mode="synthetic",
        training_seed=42,
    )

    with pytest.raises(CheckpointCompatibilityError, match="max_episode_steps"):
        validate_checkpoint_metadata(
            checkpoint,
            algorithm="dqn",
            observation_dim=14,
            action_count=3,
            max_episode_steps=250,
        )
