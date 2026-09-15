"""Regression coverage for constrained routing and controlled trace mixtures."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

import api.server as api_server
from rl.agent import RLAgent
from rl.safety import constrain_route_action, validate_path_scores
from rl.traces import (
    generate_scenario_mixture_trace,
    trace_fingerprint,
    validate_disjoint_trace_files,
    write_synchronized_trace,
)
from schemas.contracts import ROUTING_STATE_V3
from schemas.decision_event import DecisionTrace


def test_unsafe_policy_action_is_replaced_by_safest_allowed_route():
    decision = constrain_route_action(0, [0.95, 0.4, 0.2])

    assert decision.action_id == 2
    assert decision.safe_action_mask == (False, True, True)
    assert decision.safety_override is True
    assert decision.no_safe_route is False
    assert decision.constraint_reason == "policy_route_above_threshold"


def test_all_unsafe_routes_are_explicit_even_without_an_action_change():
    decision = constrain_route_action(1, [0.92, 0.81, 0.97])

    assert decision.action_id == 1
    assert decision.safe_action_mask == (False, False, False)
    assert decision.safety_override is False
    assert decision.no_safe_route is True
    assert decision.constraint_reason == "all_routes_above_threshold"
    assert decision.all_unsafe_behavior == "least_risk_route"


def test_rl_agent_exposes_all_unsafe_state_while_preserving_three_actions():
    class UnsafePolicy:
        def predict(self, _observation, deterministic=True):
            assert deterministic is True
            return 0, None

    agent = RLAgent.__new__(RLAgent)
    agent.algo = "dqn"
    agent.model = UnsafePolicy()
    agent._prev_action = None
    agent._prev_reward = 0.0
    agent._step = 0
    agent._max_steps = 500

    result = agent.predict(
        [0.91, 0.82, 0.97],
        path_latencies=[0.9, 0.8, 1.0],
        path_losses=[0.9, 0.8, 1.0],
    )

    assert result["original_action_id"] == 0
    assert result["action_id"] == 1
    assert result["path_name"] == "satellite"
    assert result["safe_action_mask"] == [False, False, False]
    assert result["safety_override"] is True
    assert result["no_safe_route"] is True


def test_v3_agent_can_build_next_observation_after_hold_action():
    agent = RLAgent.__new__(RLAgent)
    agent.routing_contract = ROUTING_STATE_V3
    agent._prev_action = 3
    agent._prev_reward = 0.0
    agent._step = 1
    agent._max_steps = 500

    observation = agent._build_obs([0.1, 0.2, 0.3])

    assert observation.shape == (18,)
    assert observation[9:12].tolist() == [0.0, 0.0, 0.0]


def test_api_greedy_fallback_uses_same_explicit_constraint_contract():
    result = api_server._greedy_predict([0.91, 0.82, 0.97])

    assert result["action_id"] == 1
    assert result["original_action_id"] == 1
    assert result["safety_override"] is False
    assert result["no_safe_route"] is True
    assert result["constraint_reason"] == "all_routes_above_threshold"


@pytest.mark.parametrize(
    "scores",
    ([0.1, 0.2], [0.1, float("nan"), 0.2], [0.1, 1.1, 0.2]),
)
def test_route_constraint_rejects_invalid_scores(scores):
    with pytest.raises(ValueError):
        validate_path_scores(scores)


def test_decision_event_constraint_state_cannot_contradict_mask():
    with pytest.raises(ValidationError, match="safe_action_mask"):
        DecisionTrace.model_validate({
            "source": "greedy_fallback",
            "policy_action_id": 0,
            "policy_path": "direct",
            "requested_action_id": 0,
            "requested_path": "direct",
            "threat_level": "HIGH",
            "no_safe_route": True,
            "safe_action_mask": [False, True, False],
            "constraint_reason": "all_routes_above_threshold",
        })


def test_controlled_mixture_is_deterministic_and_preserves_counts():
    counts = {"iid": 3, "persistent_spot": 2, "barrage": 1, "reactive": 2}
    first = generate_scenario_mixture_trace(
        episode_counts=counts,
        steps_per_episode=5,
        base_seed=700,
    )
    second = generate_scenario_mixture_trace(
        episode_counts=counts,
        steps_per_episode=5,
        base_seed=700,
    )

    pd.testing.assert_frame_equal(first, second)
    assert trace_fingerprint(first) == trace_fingerprint(second)
    assert len(first) == sum(counts.values()) * 5
    actual_counts = {
        scenario: int(group["episode_id"].nunique())
        for scenario, group in first.groupby("scenario")
    }
    assert actual_counts == counts
    assert "smart_jammer" not in set(first["scenario"])


def test_mixture_rejects_overlapping_scenario_seed_ranges():
    with pytest.raises(ValueError, match="seed ranges would overlap"):
        generate_scenario_mixture_trace(
            episode_counts={"iid": 3},
            steps_per_episode=1,
            base_seed=1,
            scenario_seed_stride=2,
        )


def test_mixture_provenance_records_distribution_and_disjointness(tmp_path):
    counts = {"iid": 2, "barrage": 1}
    training = generate_scenario_mixture_trace(
        episode_counts=counts,
        steps_per_episode=3,
        base_seed=100,
    )
    evaluation = generate_scenario_mixture_trace(
        episode_counts=counts,
        steps_per_episode=3,
        base_seed=10_000,
    )
    training_path = write_synchronized_trace(training, tmp_path / "train.csv")
    evaluation_path = write_synchronized_trace(evaluation, tmp_path / "eval.csv")

    provenance = validate_disjoint_trace_files(training_path, evaluation_path)

    assert provenance["training_scenario_episode_counts"] == counts
    assert provenance["evaluation_scenario_episode_counts"] == counts
    assert provenance["training_trace_sha256"] != provenance["evaluation_trace_sha256"]
