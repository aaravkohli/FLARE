"""Acceptance coverage for FLARE security upgrade phases 0 through 7."""

from __future__ import annotations

from collections import namedtuple

import numpy as np
import torch
import torch.nn as nn
from fastapi.testclient import TestClient

from fl.distillation import compute_ensemble_soft_labels, feddf_distillation_step
from fl.explainability import integrated_gradients
from fl.insider import InsiderTelemetryAnalyzer
from fl.trust import UpdateAnalyzer
from orchestrator.loop import Orchestrator
from rl.env import TrustAwareDronePathEnv
from rl.safety import constrain_route_action
from schemas.contracts import ROUTING_ACTIONS_V3, routing_contract
from schemas.decision_event import ContainmentTrace, InsiderTrace
from sdn.containment import ContainmentMode, decide_containment
from sdn.mock_sdn import SDN_API_TOKEN, app
from sdn.route_contract import action_id_for_path, validate_route_action


def test_phase0_versioned_v3_contract_is_explicit():
    contract = routing_contract("routing_state_v3")
    assert contract.observation_dim == 18
    assert contract.actions == ROUTING_ACTIONS_V3
    assert action_id_for_path("hold") == 3
    validate_route_action("hold", 3)


def test_phase1_heterogeneous_positive_update_is_not_rejected_but_poison_is():
    baseline = [np.zeros(3)]
    updates = [
        [np.array([1.0, 0.2, 0.1])],
        [np.array([0.9, 0.3, 0.1])],
        [np.array([0.3, 1.0, 0.2])],  # legitimate label/RF-skewed client
        [np.array([-20.0, -20.0, -20.0])],
    ]
    analyses = UpdateAnalyzer().analyze(
        ["a", "b", "heterogeneous", "poison"], updates, baseline
    )
    assert analyses[2].status != "MALICIOUS"
    assert analyses[3].status == "MALICIOUS"
    assert analyses[3].extreme_evidence is True
    assert analyses[0].adaptive_z_threshold >= 3.5


class _ConstantTeacher(nn.Module):
    def __init__(self, logits: list[float]):
        super().__init__()
        self.anchor = nn.Parameter(torch.tensor(0.0))
        self.register_buffer("logits", torch.tensor(logits, dtype=torch.float32))
        self.head_attack = namedtuple("Head", "out_features")(len(logits))
        self.head_threat = namedtuple("Head", "out_features")(3)

    def forward(self, x):
        batch = len(x)
        logits = self.logits.unsqueeze(0).repeat(batch, 1) + self.anchor * 0.0
        output = namedtuple("Output", "path_scores attack_logits")
        return output(torch.full((batch, 3), 0.5, device=x.device), logits)


def test_phase2_teacher_trust_weights_change_feddf_targets():
    first = _ConstantTeacher([8.0, 0.0])
    second = _ConstantTeacher([0.0, 8.0])
    x = torch.zeros(4, 2, 5)
    attack, _ = compute_ensemble_soft_labels(
        [first, second], x, temperature=1.0, teacher_weights=[0.95, 0.05]
    )
    assert torch.all(attack[:, 0] > 0.9)


def _evidence(**overrides):
    values = {
        "reported_tx_packets": 100,
        "controller_rx_packets": 100,
        "controller_forwarded_packets": 99,
        "reported_forwarded_packets": 99,
        "control_messages_per_s": 3.0,
        "duplicate_sequence_ratio": 0.01,
        "timestamp": 100.0,
        "controller_timestamp": 100.02,
    }
    values.update(overrides)
    return values


def test_phase3_insider_detector_uses_independent_counter_disagreement():
    analyzer = InsiderTelemetryAnalyzer(history_alpha=0.0)
    normal = analyzer.analyze("drone_1", _evidence())
    malicious = analyzer.analyze(
        "drone_2",
        _evidence(
            controller_forwarded_packets=15,
            reported_forwarded_packets=100,
            duplicate_sequence_ratio=0.8,
        ),
    )
    assert normal.status == "NORMAL"
    assert normal.predicted_class == "normal"
    assert malicious.status == "MALICIOUS"
    assert malicious.predicted_class in {"selective_forwarding", "telemetry_falsification"}


def test_telemetry_falsification_reaches_executable_restriction_with_history():
    analyzer = InsiderTelemetryAnalyzer(history_alpha=0.65)
    analysis = None
    for _ in range(10):
        analysis = analyzer.analyze(
            "drone_1",
            _evidence(
                controller_forwarded_packets=55,
                reported_forwarded_packets=100,
            ),
        )

    assert analysis is not None
    assert analysis.status == "SUSPICIOUS"
    assert analysis.predicted_class == "telemetry_falsification"
    decision = decide_containment(
        "drone_1", trust_score=0.95, insider_risk=analysis.risk_score
    )
    assert decision.mode is ContainmentMode.RESTRICTED


def test_phase4_containment_policy_and_mock_enforcement_are_executable():
    decision = decide_containment("drone_3", trust_score=0.1, insider_risk=0.95)
    assert decision.mode is ContainmentMode.QUARANTINED
    client = TestClient(app)
    response = client.post(
        "/sdn/containment",
        json={"drone_id": "drone_3", "mode": "quarantined", "reason": "test"},
        headers={"Authorization": f"Bearer {SDN_API_TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["enforcement"] == "drop_all"


def test_runtime_insider_and_containment_payloads_match_canonical_schema():
    analyzer = InsiderTelemetryAnalyzer(history_alpha=0.0)
    analysis = analyzer.analyze("drone_1", _evidence())
    containment = decide_containment(
        "drone_1", trust_score=0.8, insider_risk=analysis.risk_score
    )

    assert InsiderTrace.model_validate(analysis.to_dict()).client_id == "drone_1"
    assert ContainmentTrace.model_validate(containment.to_dict()).drone_id == "drone_1"


def test_phase5_all_unsafe_can_fail_closed_with_hold():
    decision = constrain_route_action(
        0,
        [0.91, 0.82, 0.97],
        all_unsafe_behavior="hold",
        allow_hold_action=True,
    )
    assert decision.no_safe_route is True
    assert decision.action_id == 3
    assert decision.network_action == "hold"
    response = TestClient(app).post(
        "/sdn/route",
        json={"path_name": "hold", "action_id": 3, "drone_id": "drone_1"},
        headers={"Authorization": f"Bearer {SDN_API_TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["installed_path"] == "hold"


def test_orchestrator_reward_accepts_recovery_from_hold_to_route(make_orchestrator):
    runtime = make_orchestrator()
    reward = runtime._compute_reward(
        [0.1, 0.2, 0.3],
        [0.1, 0.2, 0.3],
        [0.1, 0.2, 0.3],
        action=0,
        previous_action=3,
    )

    assert reward.held is False
    assert reward.switched is False


def test_phase6_trust_aware_environment_has_18_inputs_and_4_actions():
    env = TrustAwareDronePathEnv()
    observation, _ = env.reset(seed=42)
    assert observation.shape == (18,)
    assert env.action_space.n == 4
    env.inject_security_context(
        client_trust=0.1,
        insider_risk=0.95,
        containment_score=0.95,
        evidence_freshness=1.0,
    )
    _, hold_reward, *_ = env.step(3)
    env.reset(seed=42)
    env.inject_security_context(
        client_trust=0.1,
        insider_risk=0.95,
        containment_score=0.95,
        evidence_freshness=1.0,
    )
    _, unsafe_forward_reward, *_ = env.step(0)
    assert hold_reward > unsafe_forward_reward


class _LinearThreatModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(3, 1, bias=False)
        with torch.no_grad():
            self.linear.weight.copy_(torch.tensor([[0.5, -0.25, 0.75]]))

    def forward(self, x):
        value = self.linear(x.mean(dim=1))
        output = namedtuple("Output", "path_scores attack_logits")
        path_scores = torch.cat([value, value * 0.5, value * 0.25], dim=1)
        attack_logits = torch.cat([value, -value], dim=1)
        return output(path_scores, attack_logits)


def test_phase7_integrated_gradients_is_target_specific_and_complete():
    model = _LinearThreatModel()
    x = torch.tensor([[[0.2, 0.4, 0.8], [0.4, 0.2, 0.6]]])
    explanation = integrated_gradients(
        model, x, target_head="threat", target_index=0, steps=32
    )
    assert explanation["method"] == "Integrated Gradients"
    assert explanation["target"] == {"head": "threat", "index": 0}
    assert explanation["normalized_completeness_error"] < 1e-5
    assert explanation["faithfulness_passed"] is True
