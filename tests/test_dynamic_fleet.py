"""Dynamic fleet enrollment and cross-layer discovery regression tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.server as api_server
import fleet.registry as fleet_registry
from fl.trust import ClientIdentityRegistry
from schemas.decision_event import TelemetrySnapshot
from simulation.generator import generate_metrics


@pytest.fixture
def isolated_registry(isolated_fleet_registry, monkeypatch):
    monkeypatch.setattr(api_server, "_rl_model_path", None)
    yield isolated_fleet_registry


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/token",
        data={"username": "admin", "password": "antijam2026"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_enrollment_is_discovered_by_api_telemetry_and_fl_identity(isolated_registry):
    client = TestClient(api_server.app)
    headers = _admin_headers(client)
    payload = {
        "drone_id": "survey_4",
        "display_name": "Survey UAV 4",
        "mac": "02:00:00:00:00:04",
        "access_port": 7,
        "rssi_offset": -4.0,
        "pdr_offset": -0.02,
        "latency_factor": 1.2,
    }

    response = client.post("/fleet/drones", headers=headers, json=payload)
    assert response.status_code == 201
    assert response.json()["runtime"]["telemetry_detection"] == "active"
    assert response.json()["runtime"]["fl_identity"] == "authorized"

    fleet = client.get("/fleet/drones", headers=headers).json()["drones"]
    assert any(drone["drone_id"] == "survey_4" for drone in fleet)

    telemetry = TelemetrySnapshot.model_validate(generate_metrics("survey_4", seed=42))
    assert telemetry.drone_id == "survey_4"

    identities = ClientIdentityRegistry(
        allowed_client_resolver=fleet_registry.active_drone_ids
    )
    resolved, error = identities.resolve("flower-proxy-4", "survey_4")
    assert (resolved, error) == ("survey_4", None)


def test_enrollment_rejects_ambiguous_device_bindings(isolated_registry):
    client = TestClient(api_server.app)
    headers = _admin_headers(client)
    response = client.post(
        "/fleet/drones",
        headers=headers,
        json={
            "drone_id": "rogue_alias",
            "display_name": "Ambiguous UAV",
            "mac": "00:00:00:00:00:02",
            "access_port": 9,
        },
    )
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


def test_unenrolled_identity_remains_blocked(isolated_registry):
    identities = ClientIdentityRegistry(
        allowed_client_resolver=fleet_registry.active_drone_ids
    )
    resolved, error = identities.resolve("unknown-proxy", "not_enrolled")
    assert resolved == "unverified:unknown-proxy"
    assert "not allowed" in str(error)


def test_constructor_factory_and_live_sync_use_isolated_three_and_five_drone_registries(
    isolated_registry, make_orchestrator,
):
    initial = make_orchestrator()
    canonical_ids = fleet_registry.active_drone_ids()
    assert len(canonical_ids) == 3
    assert tuple(initial.DRONES) == canonical_ids
    assert set(initial._metric_history) == set(canonical_ids)
    assert set(initial._prev_rewards) == set(canonical_ids)
    assert initial._deployment_watcher.manifest_path.parent == initial._rl_path.parent

    for ordinal in (4, 5):
        fleet_registry.register_drone(
            drone_id=f"survey_{ordinal}",
            mac=f"02:00:00:00:00:0{ordinal}",
            access_port=ordinal + 3,
        )
    five_ids = fleet_registry.active_drone_ids()
    assert len(five_ids) == 5
    initial._sync_fleet()
    assert tuple(initial.DRONES) == five_ids
    assert set(initial._metric_history) == set(five_ids)
    assert set(initial._prev_rewards) == set(five_ids)

    restarted = make_orchestrator()
    assert tuple(restarted.DRONES) == five_ids
    assert set(restarted._containment_modes) == set(five_ids)
