"""Regression tests for API and dashboard-facing SDN readiness."""

from __future__ import annotations

import asyncio
import httpx
from fastapi.testclient import TestClient

import api.server as api_server
import sdn.mock_sdn as mock_sdn


def test_mock_controller_exposes_normalized_readiness(monkeypatch):
    monkeypatch.setattr(mock_sdn, "_available_paths", {"direct", "mesh"})

    response = TestClient(mock_sdn.app).get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["mode"] == "mock"
    assert payload["topology"]["ready"] is True
    assert payload["topology"]["available_paths"]["drone_1"] == [
        "direct",
        "mesh",
    ]


def test_api_readiness_returns_200_for_usable_sdn(monkeypatch):
    async def ready_sdn():
        return api_server.SDNReadiness(
            mode="mock",
            ready=True,
            status="ready",
            available_paths={
                drone_id: ["direct", "satellite", "mesh"]
                for drone_id in sorted(api_server.VALID_DRONE_IDS)
            },
        )

    monkeypatch.setattr(api_server, "_fetch_sdn_readiness", ready_sdn)

    response = TestClient(api_server.app).get("/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["sdn"]["available_paths"]["drone_3"] == [
        "direct",
        "satellite",
        "mesh",
    ]


def test_api_readiness_returns_503_without_sdn(monkeypatch):
    async def unavailable_sdn():
        return api_server.SDNReadiness(
            mode="ryu",
            ready=False,
            status="unreachable",
            error="SDN readiness endpoint is unavailable",
        )

    monkeypatch.setattr(api_server, "_fetch_sdn_readiness", unavailable_sdn)

    response = TestClient(api_server.app).get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["sdn"]["error"] == "SDN readiness endpoint is unavailable"


def test_controller_payload_is_reduced_to_dashboard_safe_summary(monkeypatch):
    topology = {
        "ready": True,
        "expected_switches": [1, 2, 3, 4, 5],
        "connected_switches": [1, 2, 3, 4, 5],
        "ports": {"1": {"1": "up"}},
        "available_paths": {
            "drone_1": ["satellite", "fallback", "mesh"],
            "unknown_drone": ["direct"],
        },
    }

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return httpx.Response(
                200,
                json={"status": "ready", "mode": "real", "topology": topology},
            )

    monkeypatch.setattr(api_server.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("MODE", "real")
    monkeypatch.setenv("AJ_SDN_MODE", "ryu")

    result = asyncio.run(api_server._fetch_sdn_readiness())

    assert result.ready is True
    assert result.connected_switches == 5
    assert result.expected_switches == 5
    assert result.available_paths == {"drone_1": ["satellite", "mesh"]}
    assert "ports" not in result.model_dump()


def test_controller_503_is_preserved_as_not_ready(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return httpx.Response(
                503,
                json={
                    "status": "not_ready",
                    "topology": {
                        "ready": False,
                        "expected_switches": [1, 2, 3, 4, 5],
                        "connected_switches": [1, 2],
                        "available_paths": {},
                    },
                },
            )

    monkeypatch.setattr(api_server.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(api_server._fetch_sdn_readiness())

    assert result.ready is False
    assert result.status == "not_ready"
    assert result.connected_switches == 2
    assert result.expected_switches == 5
