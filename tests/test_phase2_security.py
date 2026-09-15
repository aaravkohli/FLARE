"""Regression coverage for Phase 2 authentication and configuration hardening."""

from __future__ import annotations

import asyncio
import copy

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.server as api_server
import fleet.registry as fleet_registry
import orchestrator.loop as orchestrator
import sdn.mock_sdn as mock_sdn
from simulation.jammer import VALID_PROFILES


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_server.app)


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/token",
        data={"username": "admin", "password": "antijam2026"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_cors_allows_local_ui_and_rejects_untrusted_origin(client):
    trusted = client.options(
        "/auth/token",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == "http://localhost:5173"

    untrusted = client.options(
        "/auth/token",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in untrusted.headers


def test_health_reports_runtime_mode_and_controller(client, monkeypatch):
    monkeypatch.setenv("MODE", "simulation")
    monkeypatch.setenv("AJ_SDN_MODE", "mock")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "simulation"
    assert response.json()["sdn_controller"] == "mock"


def test_unimplemented_cyber_attacks_are_not_exposed_as_jammer_profiles():
    assert VALID_PROFILES.isdisjoint(
        {"sybil", "model_poisoning", "data_poisoning", "backdoor"}
    )
    with pytest.raises(ValueError):
        api_server.JamRequest(drone_id="drone_1", profile="sybil", paths=[])


@pytest.mark.parametrize(
    ("runtime_mode", "is_production"),
    [("real", False), ("simulation", True)],
)
def test_byzantine_attack_injection_fails_closed_outside_safe_simulation(
    monkeypatch, runtime_mode, is_production
):
    monkeypatch.setenv("MODE", runtime_mode)
    monkeypatch.setattr(api_server, "IS_PRODUCTION", is_production)

    with pytest.raises(api_server.HTTPException) as exc_info:
        api_server._require_byzantine_simulation_mode()

    assert exc_info.value.status_code == 409


def test_report_rejects_bearer_token_in_query_string(client):
    token = _login(client)
    response = client.get("/api/report/generate", params={"token": token})
    assert response.status_code == 401


def test_sse_requires_bearer_authentication(client):
    response = client.get("/stream")
    assert response.status_code == 401


def test_websocket_requires_first_message_authentication(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "auth", "token": "invalid"})
            websocket.receive_json()
    assert exc_info.value.code == 4401

    token = _login(client)
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "auth", "token": token})
        assert websocket.receive_json() == {"type": "auth", "status": "ok"}


def test_failed_logins_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(api_server, "_login_failures", {})
    for _ in range(api_server.LOGIN_MAX_FAILURES):
        response = client.post(
            "/auth/token",
            data={"username": "admin", "password": "incorrect"},
        )
        assert response.status_code == 401

    blocked = client.post(
        "/auth/token",
        data={"username": "admin", "password": "incorrect"},
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == str(api_server.LOGIN_WINDOW_SECONDS)


def test_control_routes_require_admin_role(client, monkeypatch):
    users = copy.deepcopy(api_server._USERS)
    users["viewer"] = {
        "username": "viewer",
        "hashed_password": api_server.pwd_context.hash("viewer-password"),
        "role": "viewer",
    }
    monkeypatch.setattr(api_server, "_USERS", users)
    token = api_server._create_access_token({"sub": "viewer"})

    response = client.post(
        "/swarm/restore/drone_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_insider_profile_control_is_visible_to_authenticated_dashboard(
    client, monkeypatch, tmp_path
):
    import simulation.insider_state as insider_state

    state_path = tmp_path / "insider_state.json"
    monkeypatch.setattr(insider_state, "DEFAULT_STATE_PATH", state_path)
    monkeypatch.setenv("FLARE_INSIDER_STATE_PATH", str(state_path))
    monkeypatch.setenv("MODE", "simulation")
    monkeypatch.setattr(api_server, "IS_PRODUCTION", False)
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    unauthenticated = client.get("/swarm/insider")
    assert unauthenticated.status_code == 401

    injected = client.post(
        "/swarm/insider/drone_1",
        headers=headers,
        json={"profile": "control_flood"},
    )
    assert injected.status_code == 200

    response = client.get("/swarm/insider", headers=headers)
    assert response.status_code == 200
    assert response.json()["simulation_enabled"] is True
    assert response.json()["profiles"] == {
        drone_id: "control_flood" if drone_id == "drone_1" else "normal"
        for drone_id in fleet_registry.active_drone_ids()
    }

    now = api_server.time.time()

    async def latest_events():
        return {
            "drone_1": {
                "event_id": "test:1:drone_1",
                "timestamp": now,
                "telemetry": {
                    "security_evidence": {"simulation_profile": "control_flood"}
                },
                "insider_analysis": {
                    "client_id": "drone_1",
                    "status": "MALICIOUS",
                    "predicted_class": "control_flood",
                    "risk_score": 0.72,
                },
                "containment": {
                    "drone_id": "drone_1",
                    "mode": "control_only",
                    "reason": "malicious_evidence",
                    "trust_score": 0.8,
                    "insider_risk": 0.72,
                    "decided_at": now,
                },
                "decision": {
                    "installed_path": "hold",
                    "requested_path": "hold",
                    "network_action": "hold",
                    "constraint_reason": "containment_required",
                },
                "sdn": {"applied": True},
            }
        }

    monkeypatch.setattr(api_server, "_latest_decision_events", latest_events)
    evidence = client.get("/swarm/insider/drone_1", headers=headers)
    assert evidence.status_code == 200
    payload = evidence.json()
    assert payload["available"] is True
    assert payload["profile_synchronized"] is True
    assert payload["analysis"]["predicted_class"] == "control_flood"
    assert payload["containment"]["mode"] == "control_only"
    assert payload["decision"]["installed_path"] == "hold"
    assert payload["sdn_applied"] is True


def test_fl_config_patch_is_validated_and_written_atomically(tmp_path, monkeypatch, client):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "fl_config.yaml"
    original = {
        "training": {"learning_rate": 0.001, "local_epochs": 2},
        "compression": {"enabled": True},
    }
    config_path.write_text(yaml.safe_dump(original), encoding="utf-8")
    monkeypatch.setattr(api_server, "_BASE", tmp_path)
    headers = {"Authorization": f"Bearer {_login(client)}"}

    accepted = client.post(
        "/api/fl/config",
        json={"training": {"learning_rate": 0.002}},
        headers=headers,
    )
    assert accepted.status_code == 200
    assert yaml.safe_load(config_path.read_text())["training"] == {
        "learning_rate": 0.002,
        "local_epochs": 2,
    }
    assert not list(config_dir.glob(".fl_config.yaml.*.tmp"))

    integer_encoded_float = client.post(
        "/api/fl/config",
        json={"training": {"learning_rate": 1}},
        headers=headers,
    )
    assert integer_encoded_float.status_code == 200
    stored_learning_rate = yaml.safe_load(config_path.read_text())["training"]["learning_rate"]
    assert stored_learning_rate == 1.0
    assert isinstance(stored_learning_rate, float)

    before_rejection = config_path.read_text()
    rejected = client.post(
        "/api/fl/config",
        json={"training": {"unknown_parameter": 1}},
        headers=headers,
    )
    assert rejected.status_code == 422
    assert config_path.read_text() == before_rejection

    wrong_type = client.post(
        "/api/fl/config",
        json={"training": {"local_epochs": 2.5}},
        headers=headers,
    )
    assert wrong_type.status_code == 422
    assert config_path.read_text() == before_rejection


def test_fl_protection_self_test_is_authenticated_and_non_mutating(
    tmp_path, monkeypatch, client
):
    unauthenticated = client.post("/api/fl/security/self-test")
    assert unauthenticated.status_code == 401

    users = copy.deepcopy(api_server._USERS)
    users["viewer"] = {
        "username": "viewer",
        "hashed_password": api_server.pwd_context.hash("viewer-password"),
        "role": "viewer",
    }
    monkeypatch.setattr(api_server, "_USERS", users)
    viewer_token = api_server._create_access_token({"sub": "viewer"})
    viewer_response = client.post(
        "/api/fl/security/self-test",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert viewer_response.status_code == 403

    token = _login(client)
    config_dir = tmp_path / "config"
    results_dir = tmp_path / "results"
    simulation_dir = tmp_path / "simulation"
    config_dir.mkdir()
    results_dir.mkdir()
    simulation_dir.mkdir()
    (config_dir / "fl_config.yaml").write_text("{}\n", encoding="utf-8")
    trust_state = results_dir / "fl_trust_state.json"
    client_state = simulation_dir / "byzantine_state.json"
    trust_state.write_text('{"sentinel":"live trust"}', encoding="utf-8")
    client_state.write_text('{"sentinel":"live clients"}', encoding="utf-8")
    trust_before = trust_state.read_bytes()
    client_before = client_state.read_bytes()
    monkeypatch.setattr(api_server, "_BASE", tmp_path)

    response = client.post(
        "/api/fl/security/self-test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["passed"] is True
    assert payload["global_model_modified"] is False
    assert payload["trust_history_modified"] is False
    assert payload["client_modes_modified"] is False
    assert trust_state.read_bytes() == trust_before
    assert client_state.read_bytes() == client_before


def test_mock_sdn_control_plane_requires_service_token():
    sdn_client = TestClient(mock_sdn.app)
    payload = {"path_name": "mesh", "action_id": 2, "drone_id": "drone_1"}

    rejected = sdn_client.post("/sdn/route", json=payload)
    assert rejected.status_code == 401

    accepted = sdn_client.post(
        "/sdn/route",
        json=payload,
        headers={"Authorization": f"Bearer {mock_sdn.SDN_API_TOKEN}"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["installed_path"] == "mesh"


def test_orchestrator_sends_sdn_service_token():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok", "installed_path": "direct"}

    class FakeClient:
        headers = None

        async def post(self, url, *, json, headers, timeout):
            self.headers = headers
            return FakeResponse()

    fake_client = FakeClient()
    result = asyncio.run(orchestrator._push_to_sdn(fake_client, "direct", "drone_1"))

    assert result["installed_path"] == "direct"
    assert fake_client.headers == {
        "Authorization": f"Bearer {orchestrator.SDN_API_TOKEN}"
    }
