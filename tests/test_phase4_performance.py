"""Regression coverage for Phase 4 batching and hot-path refactors."""

from __future__ import annotations

import asyncio
import csv
import sqlite3
from types import SimpleNamespace

import numpy as np
import torch

import api.server as api_server
import orchestrator.loop as orchestrator
import simulation.generator as generator


def _metrics(drone_id: str) -> dict:
    return {
        "drone_id": drone_id,
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
        })

    orchestrator._log_experiments(rows)

    assert connect_calls == 1
    with sqlite3.connect(orchestrator._DB_PATH) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    with open(orchestrator._CSV_PATH, newline="") as csv_file:
        assert len(list(csv.DictReader(csv_file))) == 3


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

