"""Regression coverage for synchronized three-path RL replay traces."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rl.env import SynchronizedTraceDronePathEnv
from rl.evaluation import evaluate_policy_set
from rl.traces import (
    TRACE_COLUMNS,
    TRACE_VERSION,
    TraceValidationError,
    generate_synchronized_trace,
    load_synchronized_trace,
    trace_fingerprint,
    validate_disjoint_trace_files,
    validate_synchronized_trace,
    write_synchronized_trace,
)


class _AlwaysDirectModel:
    def predict(self, observation, deterministic=True):
        del observation, deterministic
        return 0, None


def test_trace_generation_is_deterministic_and_contains_all_three_paths():
    first = generate_synchronized_trace(
        scenario="persistent_spot",
        n_episodes=2,
        steps_per_episode=5,
        base_seed=700,
    )
    second = generate_synchronized_trace(
        scenario="persistent_spot",
        n_episodes=2,
        steps_per_episode=5,
        base_seed=700,
    )

    pd.testing.assert_frame_equal(first, second)
    assert tuple(first.columns) == TRACE_COLUMNS
    assert set(first["trace_version"]) == {TRACE_VERSION}
    for path_name in ("direct", "satellite", "mesh"):
        assert f"{path_name}_threat_score" in first
        assert f"{path_name}_latency_norm" in first
        assert f"{path_name}_packet_loss" in first
        assert f"{path_name}_jammed" in first


def test_trace_validation_rejects_missing_and_duplicate_timesteps():
    frame = generate_synchronized_trace(
        scenario="iid",
        n_episodes=1,
        steps_per_episode=3,
        base_seed=12,
    )

    with pytest.raises(TraceValidationError, match="missing required columns"):
        validate_synchronized_trace(frame.drop(columns=["mesh_packet_loss"]))

    duplicate = frame.copy()
    duplicate.loc[1, "step"] = 0
    with pytest.raises(TraceValidationError, match="duplicate timestep"):
        validate_synchronized_trace(duplicate)


def test_trace_round_trip_preserves_validated_fingerprint(tmp_path):
    frame = generate_synchronized_trace(
        scenario="barrage",
        n_episodes=2,
        steps_per_episode=4,
        base_seed=22,
    )
    trace_path = write_synchronized_trace(frame, tmp_path / "trace.csv")

    loaded = load_synchronized_trace(trace_path)

    assert trace_fingerprint(loaded) == trace_fingerprint(frame)


def test_training_and_evaluation_trace_split_must_be_disjoint(tmp_path):
    training_path = write_synchronized_trace(
        generate_synchronized_trace(
            scenario="iid",
            n_episodes=2,
            steps_per_episode=3,
            base_seed=100,
        ),
        tmp_path / "training.csv",
    )
    evaluation_path = write_synchronized_trace(
        generate_synchronized_trace(
            scenario="iid",
            n_episodes=2,
            steps_per_episode=3,
            base_seed=200,
        ),
        tmp_path / "evaluation.csv",
    )

    provenance = validate_disjoint_trace_files(training_path, evaluation_path)
    assert provenance["training_episode_count"] == 2
    assert provenance["evaluation_episode_count"] == 2

    with pytest.raises(TraceValidationError, match="share episode IDs"):
        validate_disjoint_trace_files(training_path, training_path)


def test_trace_environment_replays_exact_rows_and_terminates(tmp_path):
    frame = generate_synchronized_trace(
        scenario="iid",
        n_episodes=1,
        steps_per_episode=2,
        base_seed=4,
    )
    frame.loc[0, [
        "direct_threat_score",
        "satellite_threat_score",
        "mesh_threat_score",
    ]] = [0.11, 0.22, 0.33]
    frame.loc[1, [
        "direct_threat_score",
        "satellite_threat_score",
        "mesh_threat_score",
    ]] = [0.44, 0.55, 0.66]
    trace_path = write_synchronized_trace(frame, tmp_path / "trace.csv")
    environment = SynchronizedTraceDronePathEnv(trace_path)

    observation, reset_info = environment.reset(seed=0)
    np.testing.assert_allclose(observation[:3], [0.11, 0.22, 0.33])
    assert reset_info["episode_steps"] == 2

    next_observation, _, terminated, _, info = environment.step(0)
    assert terminated is False
    np.testing.assert_allclose(next_observation[:3], [0.44, 0.55, 0.66])
    assert info["trace_step"] == 0

    _, _, terminated, _, info = environment.step(0)
    assert terminated is True
    assert info["trace_step"] == 1


def test_validation_environment_cycles_from_a_fixed_seed(tmp_path):
    frame = generate_synchronized_trace(
        scenario="iid",
        n_episodes=3,
        steps_per_episode=2,
        base_seed=44,
    )
    trace_path = write_synchronized_trace(frame, tmp_path / "trace.csv")
    environment = SynchronizedTraceDronePathEnv(
        trace_path,
        cycle_episodes=True,
    )

    _, first = environment.reset(seed=1)
    _, second = environment.reset()
    _, third = environment.reset()
    _, rewound = environment.reset(seed=1)

    expected_ids = tuple(frame["episode_id"].drop_duplicates())
    assert first["episode_id"] == expected_ids[1]
    assert second["episode_id"] == expected_ids[2]
    assert third["episode_id"] == expected_ids[0]
    assert rewound["episode_id"] == expected_ids[1]


def test_paired_evaluation_on_trace_is_reproducible_and_fingerprinted(tmp_path):
    frame = generate_synchronized_trace(
        scenario="smart_jammer",
        n_episodes=3,
        steps_per_episode=5,
        base_seed=80,
    )
    trace_path = write_synchronized_trace(frame, tmp_path / "trace.csv")
    factory = lambda: SynchronizedTraceDronePathEnv(trace_path)

    first = evaluate_policy_set(
        _AlwaysDirectModel(),
        n_episodes=3,
        base_seed=0,
        env_factory=factory,
    )
    second = evaluate_policy_set(
        _AlwaysDirectModel(),
        n_episodes=3,
        base_seed=0,
        env_factory=factory,
    )

    assert first == second
    assert first["protocol"]["scenario"] == "smart_jammer"
    assert first["protocol"]["trace_fingerprint"] == trace_fingerprint(frame)
    assert first["protocol"]["steps_per_episode"] == 5
