"""Standard-DQN routing explanation integration tests."""

from __future__ import annotations

from types import SimpleNamespace

import torch
from fastapi.testclient import TestClient

import api.server as api_server
from rl.agent import RLAgent


def _agent() -> RLAgent:
    agent = RLAgent.__new__(RLAgent)
    agent.algo = "dqn"
    agent.routing_contract = "routing_state_v2"
    network = torch.nn.Linear(14, 3, bias=True)
    with torch.no_grad():
        network.weight.fill_(0.1)
        network.bias.zero_()
    agent.model = SimpleNamespace(q_net=network)
    return agent


def test_dqn_explanation_names_every_exact_observation_value():
    explanation = _agent().explain([0.25] * 14, 1)
    assert explanation["routing_contract"] == "routing_state_v2"
    assert explanation["action"] == "satellite"
    assert len(explanation["feature_attributions"]) == 14
    assert explanation["normalized_completeness_error"] < 0.01


def test_api_explanation_keeps_policy_and_installed_action_distinct(monkeypatch):
    monkeypatch.setitem(api_server._rl_agents, "drone_1", _agent())
    monkeypatch.setattr(api_server, "_rl_model_path", None)
    result = api_server._routing_explanation_for_event("drone_1", {
        "event_id": "run:1:drone_1",
        "decision": {
            "source": "rl_model",
            "observation": [0.25] * 14,
            "policy_action_id": 0,
            "policy_path": "direct",
            "installed_action_id": 3,
            "installed_path": "hold",
            "safety_override": True,
            "model_id": None,
            "model_generation": 2,
        },
    })
    assert result["available"] is True
    assert result["policy_path"] == "direct"
    assert result["installed_path"] == "hold"
    assert result["safety_override"] is True


def test_routing_explanation_endpoint_requires_authentication():
    response = TestClient(api_server.app).get("/explanations/routing/drone_1")
    assert response.status_code == 401
