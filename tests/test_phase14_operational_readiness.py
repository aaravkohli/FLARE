"""Tests for bounded SDN readiness history and operator alerts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.readiness_history import ReadinessHistory
import api.server as api_server


DRONES = ("drone_1", "drone_2", "drone_3")
ROUTES = ("direct", "satellite", "mesh")


def _history(max_entries: int = 10) -> ReadinessHistory:
    return ReadinessHistory(
        max_entries=max_entries,
        drone_ids=DRONES,
        route_names=ROUTES,
    )


def _state(*, ready=True, status="ready", available_paths=None, **overrides):
    state = {
        "mode": "ryu",
        "ready": ready,
        "status": status,
        "connected_switches": 5 if ready else 2,
        "expected_switches": 5,
        "available_paths": (
            {drone_id: list(ROUTES) for drone_id in DRONES}
            if available_paths is None
            else available_paths
        ),
        "error": None,
    }
    state.update(overrides)
    return state


def test_identical_polls_create_one_transition():
    history = _history()

    first = history.record(_state(), timestamp=10.0)
    second = history.record(_state(), timestamp=20.0)

    assert first == second
    assert first["timestamp"] == 10.0
    assert first["severity"] == "info"
    assert len(history.snapshot(limit=10)) == 1


def test_degradation_and_recovery_are_explicit_transitions():
    history = _history()
    history.record(_state(), timestamp=10.0)
    degraded_paths = {drone_id: list(ROUTES) for drone_id in DRONES}
    degraded_paths["drone_2"] = ["satellite", "mesh"]

    degraded = history.record(
        _state(available_paths=degraded_paths),
        timestamp=20.0,
    )
    recovered = history.record(_state(), timestamp=30.0)

    assert degraded["severity"] == "warning"
    assert degraded["unavailable_paths"]["drone_2"] == ["direct"]
    assert "drone_2: direct" in degraded["message"]
    assert recovered["severity"] == "info"
    assert [event["timestamp"] for event in history.snapshot(limit=3)] == [
        30.0,
        20.0,
        10.0,
    ]


def test_unreachable_state_is_critical_and_history_is_bounded():
    history = _history(max_entries=2)
    history.record(_state(), timestamp=10.0)
    critical = history.record(
        _state(
            ready=False,
            status="unreachable",
            connected_switches=0,
            error="endpoint unavailable",
            available_paths={},
        ),
        timestamp=20.0,
    )
    history.record(_state(), timestamp=30.0)

    assert critical["severity"] == "critical"
    assert [event["timestamp"] for event in history.snapshot(limit=10)] == [
        30.0,
        20.0,
    ]


def test_readiness_endpoint_returns_current_alert(monkeypatch):
    history = _history()
    paths = {drone_id: list(ROUTES) for drone_id in DRONES}
    paths["drone_1"] = ["satellite", "mesh"]

    async def degraded_sdn():
        return api_server.SDNReadiness(
            mode="ryu",
            ready=True,
            status="ready",
            connected_switches=5,
            expected_switches=5,
            available_paths=paths,
        )

    monkeypatch.setattr(api_server, "_readiness_history", history)
    monkeypatch.setattr(api_server, "_fetch_sdn_readiness", degraded_sdn)

    response = TestClient(api_server.app).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["alert"]["severity"] == "warning"
    assert payload["alert"]["unavailable_paths"]["drone_1"] == ["direct"]


def test_readiness_history_requires_auth_and_returns_newest_first(monkeypatch):
    history = _history()
    history.record(_state(), timestamp=10.0)
    history.record(
        _state(ready=False, status="not_ready", available_paths={}),
        timestamp=20.0,
    )
    monkeypatch.setattr(api_server, "_readiness_history", history)
    client = TestClient(api_server.app)

    assert client.get("/ready/history").status_code == 401
    token_response = client.post(
        "/auth/token",
        data={"username": "admin", "password": "antijam2026"},
    )
    token = token_response.json()["access_token"]
    response = client.get(
        "/ready/history",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["capacity"] == history.max_entries
    assert payload["events"][0]["timestamp"] == 20.0
