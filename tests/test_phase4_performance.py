"""Regression coverage for Phase 4 batching and hot-path refactors."""

from __future__ import annotations

import asyncio
import csv
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from pydantic import ValidationError

import api.server as api_server
import orchestrator.loop as orchestrator
import simulation.generator as generator
from schemas.decision_event import DecisionEvent
from rl.reward import REWARD_DEFINITION, compute_routing_reward


def _metrics(drone_id: str) -> dict:
    return {
        "drone_id": drone_id,
        "timestamp": 1_800_000_000.0,
        "source": "synthetic",
        "paths": [
            {
                "path_id": path_id,
                "rssi": -55.0 - index,
                "pdr": 0.9,
                "sinr": 18.0,
                "latency": 20.0 + index,
                "packet_loss": 0.02,
            }
            for index, path_id in enumerate(["direct", "satellite", "mesh"])
        ],
    }


def _decision_event(drone_id: str = "drone_1") -> DecisionEvent:
    reward = compute_routing_reward(
        0,
        [0.1, 0.3, 0.7],
        [0.2, 0.07, 0.04],
        [0.02, 0.02, 0.02],
    )
    return DecisionEvent.model_validate({
        "event_id": f"test-run:1:{drone_id}",
        "run_id": "test-run",
        "step": 1,
        "timestamp": 1_800_000_000.5,
        "drone_id": drone_id,
        "mode": "simulation",
        "telemetry": _metrics(drone_id),
        "inference": {
            "path_scores": [0.1, 0.3, 0.7],
            "confidence": 0.4,
            "attack_type": "unknown",
            "source": "heuristic_fallback",
        },
        "decision": {
            "source": "greedy_fallback",
            "policy_action_id": 0,
            "policy_path": "direct",
            "requested_action_id": 0,
            "requested_path": "direct",
            "installed_action_id": 0,
            "installed_path": "direct",
            "threat_level": "HIGH",
            "route_changed": True,
        },
        "sdn": {"applied": True, "response": {"installed_path": "direct"}},
        "outcome": {
            "reward": reward.total,
            "reward_definition": REWARD_DEFINITION,
            "reward_components": reward.as_dict(),
            "path": "direct",
            "packet_loss": 0.02,
        },
        "fallback_reasons": ["fl_model_unavailable", "rl_model_unavailable"],
    })


class CountingThreatModel:
    def __init__(self):
        self.calls = 0
        self.batch_sizes = []

    def __call__(self, x):
        self.calls += 1
        self.batch_sizes.append(x.shape[0])
        batch_size = x.shape[0]
        return SimpleNamespace(
            path_scores=torch.full((batch_size, 3), 0.2),
            confidence=torch.full((batch_size, 1), 0.75),
            attack_logits=torch.tensor(
                [[2.0, 0.0, 0.0, 0.0, 0.0]] * batch_size
            ),
        )


def test_swarm_fl_inference_uses_one_batched_model_call():
    model = CountingThreatModel()
    metrics_by_drone = {
        drone_id: _metrics(drone_id)
        for drone_id in ["drone_1", "drone_2", "drone_3"]
    }

    result = orchestrator._fl_infer_batch(model, metrics_by_drone)

    assert model.calls == 1
    assert model.batch_sizes == [9]
    assert set(result) == set(metrics_by_drone)
    assert all(len(result[drone_id]["path_scores"]) == 3 for drone_id in result)


def test_xai_ablation_uses_one_six_variant_model_call():
    model = CountingThreatModel()
    attribution = api_server._calculate_xai_attributions(
        model,
        _metrics("drone_1")["paths"][0],
    )

    assert model.calls == 1
    assert model.batch_sizes == [6]
    assert sum(attribution.values()) == 100


def test_experiment_batch_uses_one_database_transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_DB_PATH", tmp_path / "experiment.db")
    monkeypatch.setattr(orchestrator, "_CSV_PATH", tmp_path / "run.csv")
    orchestrator._init_experiment_store()

    connect_calls = 0

    def counted_connect():
        nonlocal connect_calls
        connect_calls += 1
        connection = sqlite3.connect(orchestrator._DB_PATH)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    monkeypatch.setattr(orchestrator, "_connect_db", counted_connect)
    rows = []
    for index, drone_id in enumerate(["drone_1", "drone_2", "drone_3"]):
        rows.append({
            "timestamp": 1_800_000_000.0,
            "run_id": "batch-run",
            "step": 1,
            "drone_id": drone_id,
            "action_id": index,
            "path_name": ["direct", "satellite", "mesh"][index],
            "threat_level": "LOW",
            "reward": 0.5,
            "recovery_ms": None,
            "packet_loss": 0.01,
            "fl_confidence": 0.8,
            "attack_type": "none",
            "event_json": _decision_event(drone_id).model_dump_json(),
        })

    orchestrator._log_experiments(rows)

    assert connect_calls == 1
    with sqlite3.connect(orchestrator._DB_PATH) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    with open(orchestrator._CSV_PATH, newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        assert len(list(reader)) == 3
        assert "event_json" not in reader.fieldnames


def test_canonical_event_requires_all_three_unique_paths():
    payload = _decision_event().model_dump(mode="json")
    payload["telemetry"]["paths"][-1]["path_id"] = "direct"

    with pytest.raises(ValidationError, match="exactly once"):
        DecisionEvent.model_validate(payload)


def test_constraint_mask_must_match_decision_driving_scores():
    payload = _decision_event().model_dump(mode="json")
    payload["inference"]["path_scores"] = [0.9, 0.85, 0.95]

    with pytest.raises(ValidationError, match="safe_action_mask"):
        DecisionEvent.model_validate(payload)

    payload["decision"].update({
        "safe_action_mask": [False, False, False],
        "no_safe_route": True,
        "constraint_reason": "all_routes_above_threshold",
    })
    event = DecisionEvent.model_validate(payload)
    assert event.decision.no_safe_route is True


def test_orchestrator_persists_the_exact_decision_driving_telemetry(monkeypatch):
    class SafetyOverrideAgent:
        def predict(self, _scores, **_kwargs):
            return {
                "action_id": 2,
                "path_name": "mesh",
                "threat_level": "HIGH",
                "decision_source": "rl_model",
                "observation": [0.0] * 14,
                "safety_override": True,
                "original_action_id": 0,
            }

    runtime = orchestrator.Orchestrator.__new__(orchestrator.Orchestrator)
    runtime.DRONES = ["drone_1", "drone_2", "drone_3"]
    runtime._step = 0
    runtime._fl_model = None
    runtime._fl_model_id = None
    runtime._rl_model_id = None
    runtime._rl_agents = {"drone_1": SafetyOverrideAgent()}
    runtime._prev_rewards = {drone_id: 0.0 for drone_id in runtime.DRONES}
    runtime._prev_path = {drone_id: None for drone_id in runtime.DRONES}
    runtime._prev_path_time = {drone_id: None for drone_id in runtime.DRONES}
    runtime._last_good_decision = {drone_id: None for drone_id in runtime.DRONES}
    runtime._last_good_ts = {drone_id: 0.0 for drone_id in runtime.DRONES}
    telemetry = {drone_id: _metrics(drone_id) for drone_id in runtime.DRONES}

    async def collect_metrics(_client):
        return telemetry

    async def push_to_sdn(_client, path_name, _drone_id):
        return {"status": "ok", "installed_path": path_name}

    stored_rows = []
    runtime._collect_metrics = collect_metrics
    monkeypatch.setattr(orchestrator, "_push_to_sdn", push_to_sdn)
    monkeypatch.setattr(orchestrator, "_log_experiments", stored_rows.extend)

    asyncio.run(runtime._run_step(SimpleNamespace()))

    assert len(stored_rows) == 3
    for row in stored_rows:
        event = DecisionEvent.model_validate_json(row["event_json"])
        assert event.telemetry.model_dump(exclude_none=True) == telemetry[event.drone_id]
        assert event.inference.source == "heuristic_fallback"
        assert event.sdn.applied is True
        assert event.outcome.estimated is False
        if event.drone_id == "drone_1":
            assert event.decision.source == "rl_model"
            assert event.decision.policy_path == "direct"
            assert event.decision.requested_path == "mesh"
            assert event.decision.safety_override is True
        else:
            assert event.decision.source == "greedy_fallback"


def test_live_and_swarm_metrics_prefer_persisted_canonical_events(
    tmp_path,
    monkeypatch,
):
    experiments_dir = tmp_path / "experiments"
    experiments_dir.mkdir()
    db_path = experiments_dir / "experiment.db"
    monkeypatch.setattr(orchestrator, "_DB_PATH", db_path)
    monkeypatch.setattr(orchestrator, "_CSV_PATH", experiments_dir / "run.csv")
    monkeypatch.setattr(api_server, "_BASE", tmp_path)
    orchestrator._init_experiment_store()

    event = _decision_event("drone_1")
    orchestrator._log_experiment({
        "timestamp": event.timestamp,
        "run_id": event.run_id,
        "step": event.step,
        "drone_id": event.drone_id,
        "action_id": event.decision.installed_action_id,
        "path_name": event.decision.installed_path,
        "threat_level": event.decision.threat_level,
        "reward": event.outcome.reward,
        "recovery_ms": event.outcome.recovery_ms,
        "packet_loss": event.outcome.packet_loss,
        "fl_confidence": event.inference.confidence,
        "attack_type": event.inference.attack_type,
        "event_json": event.model_dump_json(),
    })

    live = asyncio.run(api_server.live_metrics("drone_1", {}))
    swarm = asyncio.run(api_server.swarm_all_metrics({}))

    assert live == event.telemetry.model_dump(mode="json")
    assert swarm["drone_1"] == live
    assert swarm["drone_2"]["source"] == "legacy_api_fallback"
    assert swarm["drone_3"]["source"] == "legacy_api_fallback"

    with sqlite3.connect(db_path) as connection:
        raw_event = connection.execute("SELECT event_json FROM runs").fetchone()[0]
    assert json.loads(raw_event)["event_id"] == event.event_id


def test_swarm_generation_reads_one_consistent_jammer_snapshot(monkeypatch):
    load_calls = 0
    state = {
        drone_id: {"profile": "none", "paths": [], "gps_drift": 0.0}
        for drone_id in generator.DRONES
    }

    def load_state():
        nonlocal load_calls
        load_calls += 1
        return state

    monkeypatch.setattr(generator, "_load_jam_state", load_state)
    metrics = generator.generate_swarm_metrics()

    assert load_calls == 1
    assert set(metrics) == set(generator.DRONES)


def test_real_sensor_collection_awaits_all_async_responses(monkeypatch):
    class FakeResponse:
        def __init__(self, drone_id):
            self.drone_id = drone_id

        def raise_for_status(self):
            return None

        def json(self):
            return _metrics(self.drone_id)

    class FakeAsyncClient:
        calls = []

        async def get(self, url, *, params, timeout):
            self.calls.append((url, params["drone_id"], timeout))
            await asyncio.sleep(0)
            return FakeResponse(params["drone_id"])

    monkeypatch.setattr(orchestrator, "MODE", "real")
    instance = orchestrator.Orchestrator.__new__(orchestrator.Orchestrator)
    instance.DRONES = ["drone_1", "drone_2", "drone_3"]
    client = FakeAsyncClient()

    result = asyncio.run(instance._collect_metrics(client))

    assert set(result) == set(instance.DRONES)
    assert len(client.calls) == 3
