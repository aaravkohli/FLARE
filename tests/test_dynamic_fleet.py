"""Dynamic fleet enrollment and cross-layer discovery regression tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.server as api_server
import fleet.registry as fleet_registry
import sdn.mock_sdn as mock_sdn
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


def test_admin_can_edit_display_name_and_simulation_profile(isolated_registry):
    client = TestClient(api_server.app)
    headers = _admin_headers(client)
    response = client.patch(
        "/fleet/drones/drone_3",
        headers=headers,
        json={"display_name": "Survey Three", "rssi_offset": -3.0},
    )
    assert response.status_code == 200
    row = response.json()["drone"]
    assert row["display_name"] == "Survey Three"
    assert row["rf_profile"]["rssi_offset"] == -3.0
    assert row["mac"] == "00:00:00:00:00:04"
    assert row["access_port"] == 6
    assert fleet_registry.get_drone("drone_3")["display_name"] == "Survey Three"


def test_browser_preflight_permits_authenticated_fleet_patch(isolated_registry):
    response = TestClient(api_server.app).options(
        "/fleet/drones/drone_3",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_soft_disable_is_reversible_and_preserves_reserved_identity(isolated_registry):
    client = TestClient(api_server.app)
    headers = _admin_headers(client)
    disabled = client.patch(
        "/fleet/drones/drone_3", headers=headers, json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert "drone_3" not in fleet_registry.active_drone_ids()
    assert fleet_registry.get_drone("drone_3", enabled_only=False)["enabled"] is False
    assert "drone_3" not in {
        drone["drone_id"] for drone in client.get("/fleet/drones", headers=headers).json()["drones"]
    }
    assert "drone_3" in {
        drone["drone_id"]
        for drone in client.get("/fleet/drones/manage", headers=headers).json()["drones"]
    }
    duplicate = client.post(
        "/fleet/drones", headers=headers,
        json={
            "drone_id": "reuse_3", "display_name": "Duplicate MAC",
            "mac": "00:00:00:00:00:04", "access_port": 9,
        },
    )
    assert duplicate.status_code == 409
    enabled = client.patch(
        "/fleet/drones/drone_3", headers=headers, json={"enabled": True}
    )
    assert enabled.status_code == 200
    assert "drone_3" in fleet_registry.active_drone_ids()


def test_fleet_edit_rejects_untrusted_or_unsafe_changes(isolated_registry, monkeypatch):
    client = TestClient(api_server.app)
    assert client.patch(
        "/fleet/drones/drone_3", json={"display_name": "Unknown"}
    ).status_code == 401
    headers = _admin_headers(client)
    assert client.patch(
        "/fleet/drones/drone_3", headers=headers, json={"access_port": 9}
    ).status_code == 422
    assert client.patch(
        "/fleet/drones/drone_3", headers=headers, json={"enabled": None}
    ).status_code == 400
    monkeypatch.setattr(api_server, "_runtime_mode_and_sdn_controller", lambda: ("real", "ryu"))
    assert client.patch(
        "/fleet/drones/drone_3", headers=headers, json={"enabled": False}
    ).status_code == 409
    assert client.patch(
        "/fleet/drones/drone_3", headers=headers, json={"display_name": "Safe Rename"}
    ).status_code == 200


def test_mock_sdn_prunes_disabled_identity_from_displayed_state(isolated_registry, monkeypatch):
    monkeypatch.setattr(mock_sdn, "_flow_table", {"drone_3": {"path": "direct"}})
    monkeypatch.setattr(mock_sdn, "_containment_table", {"drone_3": {"mode": "restricted"}})
    fleet_registry.update_drone("drone_3", enabled=False)
    response = TestClient(mock_sdn.app).get(
        "/sdn/flows",
        headers={"Authorization": f"Bearer {mock_sdn.SDN_API_TOKEN}"},
    )
    assert response.status_code == 200
    assert "drone_3" not in response.json()["flow_table"]
    assert "drone_3" not in response.json()["containment"]


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
