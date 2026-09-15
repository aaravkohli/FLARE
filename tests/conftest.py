"""Repository-wide test isolation fixtures."""

from __future__ import annotations

import shutil
from collections import deque
from pathlib import Path

import pytest

import fleet.registry as fleet_registry
import orchestrator.loop as orchestrator


_FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "fleet_registry.yaml"


@pytest.fixture(autouse=True)
def isolated_fleet_registry(tmp_path, monkeypatch):
    """Keep every test independent from the operator's mutable fleet registry."""
    registry_path = tmp_path / "fleet_registry.yaml"
    shutil.copyfile(_FIXTURE_REGISTRY, registry_path)
    monkeypatch.setattr(fleet_registry, "_REGISTRY_PATH", registry_path)
    fleet_registry._CACHE = None
    fleet_registry._CACHE_MTIME_NS = None
    try:
        yield registry_path
    finally:
        fleet_registry._CACHE = None
        fleet_registry._CACHE_MTIME_NS = None


@pytest.fixture
def make_orchestrator(tmp_path, monkeypatch, isolated_fleet_registry):
    """Construct complete test state without live models, storage, or fleet data."""
    monkeypatch.setattr(orchestrator, "_BASE", tmp_path)
    monkeypatch.setattr(orchestrator, "_load_fl_model", lambda: None)
    monkeypatch.setattr(orchestrator, "_init_experiment_store", lambda: None)

    def build(drone_ids=None, *, freeze_fleet=False):
        runtime = orchestrator.Orchestrator()
        if drone_ids is not None:
            identities = list(drone_ids)
            if len(identities) != len(set(identities)):
                raise ValueError("test orchestrator identities must be unique")
            runtime.DRONES = identities
            runtime._prev_path = {drone_id: None for drone_id in identities}
            runtime._prev_path_time = {drone_id: None for drone_id in identities}
            runtime._last_good_decision = {drone_id: None for drone_id in identities}
            runtime._last_good_ts = {drone_id: 0.0 for drone_id in identities}
            runtime._metric_history = {
                drone_id: deque(maxlen=runtime._sequence_len)
                for drone_id in identities
            }
            runtime._prev_rewards = {drone_id: 0.0 for drone_id in identities}
            runtime._rl_agents = {}
            runtime._containment_modes = {
                drone_id: "normal" for drone_id in identities
            }
            runtime._live_telemetry_cache = {}
            runtime._telemetry_collection_status = {}
        if freeze_fleet:
            runtime._sync_fleet = lambda: None
        return runtime

    return build
