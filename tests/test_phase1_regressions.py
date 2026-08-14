"""Regression coverage for Phase 1 correctness and data-integrity fixes."""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import api.server as api_server
import orchestrator.loop as orchestrator
import simulation.jammer as jammer
import rl.agent as rl_agent_module
from api.server import JamRequest, PredictRequest
from rl.agent import RLAgent
from rl.env import DronePathEnv
from scripts.migrate_experiment_db import migrate


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/token",
        data={"username": "admin", "password": "antijam2026"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize(
    "payload",
    [
        {"path_scores": [0.1, 0.2, 0.3], "drone_id": "drone_99"},
        {"path_scores": [0.1, float("nan"), 0.3]},
        {"path_scores": [0.1, 0.2, 0.3], "unexpected": True},
    ],
)
def test_predict_request_rejects_invalid_contract(payload):
    with pytest.raises(ValidationError):
        PredictRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"paths": ["invalid"]},
        {"paths": ["direct", "direct"]},
        {"paths": []},
        {"paths": [], "profile": "not-a-profile"},
        {"paths": [], "duration": float("inf")},
        {"paths": [], "drone_id": "drone_99"},
        {"paths": [], "extra": "not allowed"},
    ],
)
def test_jam_request_rejects_invalid_contract(payload):
    with pytest.raises(ValidationError):
        JamRequest.model_validate(payload)


def test_jammer_state_updates_are_complete_and_clearable(tmp_path, monkeypatch):
    state_path = tmp_path / "jam_state.json"
    monkeypatch.setattr(jammer, "_JAM_STATE_FILE", state_path)

    targets = jammer.set_jamming_state("all", ["direct"], "gps_spoofing")
    assert targets == ["drone_1", "drone_2", "drone_3"]
    state = json.loads(state_path.read_text())
    assert all(state[target]["gps_drift"] == 120.0 for target in targets)

    jammer.clear_jamming("all")
    state = jammer._load_state()
    assert all(state[target]["profile"] == "none" for target in targets)
    assert list(tmp_path.glob(".jam_state.*.tmp")) == []


def test_clear_jam_endpoint_no_longer_hits_drones_scope_bug(tmp_path, monkeypatch):
    monkeypatch.setattr(jammer, "_JAM_STATE_FILE", tmp_path / "jam_state.json")
    client = TestClient(api_server.app)
    headers = _auth_headers(client)

    response = client.post(
        "/jam",
        json={"paths": [], "duration": 0, "drone_id": "all", "profile": "none"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "cleared", "drone_id": "all"}


def test_experiment_insert_uses_database_column_order(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_DB_PATH", tmp_path / "experiment.db")
    monkeypatch.setattr(orchestrator, "_CSV_PATH", tmp_path / "run.csv")
    orchestrator._init_experiment_store()

    row = {
        "timestamp": 1_777_777_777.5,
        "run_id": "run-abcd",
        "step": 3,
        "drone_id": "drone_2",
        "action_id": 1,
        "path_name": "satellite",
        "threat_level": "HIGH",
        "reward": 0.25,
        "recovery_ms": 20.0,
        "packet_loss": 0.1,
        "fl_confidence": 0.8,
        "attack_type": "spot",
    }
    orchestrator._log_experiment(row)

    with sqlite3.connect(orchestrator._DB_PATH) as conn:
        stored = conn.execute(
            "SELECT run_id, timestamp, drone_id, path_name FROM runs"
        ).fetchone()
    assert stored == ("run-abcd", 1_777_777_777.5, "drone_2", "satellite")


def test_database_migration_is_dry_run_by_default_and_backed_up(tmp_path):
    db_path = tmp_path / "experiment.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id TEXT, timestamp REAL)"
        )
        conn.execute(
            "INSERT INTO runs (run_id, timestamp) VALUES (?, ?)",
            (1_777_777_777.5, "run-abcd"),
        )

    assert migrate(db_path, apply=False) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT run_id, timestamp FROM runs").fetchone() == (
            "1777777777.5",
            "run-abcd",
        )

    assert migrate(db_path, apply=True) == 1
    assert list(tmp_path.glob("experiment.backup-*.db"))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT run_id, timestamp FROM runs").fetchone() == (
            "run-abcd",
            1_777_777_777.5,
        )


def test_negative_previous_reward_is_inside_environment_space():
    env = DronePathEnv()
    env._prev_reward = -1.25
    observation = env._build_obs()
    assert env.observation_space.contains(observation)


def test_rl_agent_uses_supplied_previous_reward_in_current_observation():
    class FakeModel:
        observation = None

        def predict(self, observation, deterministic=True):
            self.observation = observation.copy()
            return 0, None

    agent = RLAgent.__new__(RLAgent)
    agent.algo = "dqn"
    agent.model = FakeModel()
    agent._prev_action = 0
    agent._prev_reward = 0.0
    agent._step = 0
    agent._max_steps = 500

    agent.predict([0.1, 0.2, 0.3], reward=-0.75)
    assert agent.model.observation[12] == pytest.approx(-0.75)


def test_orchestrator_falls_back_when_rl_checkpoint_is_incompatible(
    tmp_path,
    monkeypatch,
):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "rl_model.zip").write_bytes(b"stale-checkpoint")

    class IncompatibleAgent:
        def __init__(self, _model_path):
            raise ValueError("observation spaces do not match")

    monkeypatch.setattr(orchestrator, "_BASE", tmp_path)
    monkeypatch.setattr(orchestrator, "_load_fl_model", lambda: None)
    monkeypatch.setattr(orchestrator, "_init_experiment_store", lambda: None)
    monkeypatch.setattr(rl_agent_module, "RLAgent", IncompatibleAgent)

    runtime = orchestrator.Orchestrator()

    assert runtime._rl_agents == {}


def test_sparse_report_discloses_insufficient_data(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "_BASE", tmp_path)
    client = TestClient(api_server.app)
    headers = _auth_headers(client)

    response = client.get("/api/report/generate", headers=headers)

    assert response.status_code == 200
    assert "Insufficient experiment data" in response.text
    assert "1852" not in response.text
