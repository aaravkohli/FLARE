"""Ordered, evidence-backed route-transition journal and replay contract."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.server as api_server
import orchestrator.loop as orchestrator
from schemas.decision_event import DecisionEvent, RouteRequestStartedV1


# make_orchestrator deliberately disables storage; keep the real initializer.
INITIALIZE_STORE = orchestrator._init_experiment_store


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
            for index, path_id in enumerate(("direct", "satellite", "mesh"))
        ],
    }


@pytest.fixture
def journal_runtime(tmp_path, monkeypatch, make_orchestrator):
    runtime = make_orchestrator(["drone_1"], freeze_fleet=True)
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    db_path = experiments / "experiment.db"
    monkeypatch.setattr(orchestrator, "_DB_PATH", db_path)
    monkeypatch.setattr(orchestrator, "_CSV_PATH", experiments / "run.csv")
    monkeypatch.setattr(api_server, "_get_db_path", lambda: db_path)
    INITIALIZE_STORE()
    runtime._mission_journal_enabled = True
    runtime._refresh_deployment = lambda: None

    async def collect_metrics(_client):
        return {"drone_1": _metrics("drone_1")}

    runtime._collect_metrics = collect_metrics
    return runtime, db_path


def _rows(db_path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()


def _auth_token() -> str:
    return api_server._create_access_token({"sub": api_server.ADMIN_USERNAME})


def test_route_request_is_committed_before_controller_response(
    journal_runtime, monkeypatch,
):
    runtime, db_path = journal_runtime
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_push(_client, path_name, _drone_id):
        entered.set()
        await release.wait()
        return {"status": "ok", "installed_path": path_name}

    monkeypatch.setattr(orchestrator, "_push_to_sdn", blocked_push)

    async def scenario():
        task = asyncio.create_task(runtime._run_step(SimpleNamespace()))
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        assert _rows(db_path, "runs") == []
        pending, has_more = await api_server._mission_events_after(0, 100)
        assert has_more is False
        assert len(pending) == 1
        request = pending[0]
        assert request["phase"] == "requested"
        assert request["transition_id"] == f"{orchestrator.RUN_ID}:1:drone_1"
        assert request["decision"]["policy_path"] == "direct"
        assert request["decision"]["requested_path"] == "direct"
        assert request["last_confirmed_path"] is None
        assert request["controller"] == {
            "configured_backend": "mock",
            "status": "awaiting_response",
            "applied": None,
        }
        assert request["telemetry"]["source"] == "synthetic"
        assert request["inference"]["source"] == "heuristic_fallback"
        release.set()
        await task
        completed, _ = await api_server._mission_events_after(request["cursor"], 100)
        assert len(completed) == 1
        result = completed[0]
        assert result["phase"] == "completed"
        assert result["transition_id"] == request["transition_id"]
        assert result["cursor"] > request["cursor"]
        assert result["occurred_at"] >= request["occurred_at"]
        assert result["controller"]["status"] == "acknowledged"
        assert result["confirmed_installed_path"] == "direct"
        assert DecisionEvent.model_validate(result["event"])

    asyncio.run(scenario())
    assert len(_rows(db_path, "runs")) == 1
    assert len(_rows(db_path, "mission_event_log")) == 2


@pytest.mark.parametrize("error", ["controller uncertain", "controller timeout"])
def test_unconfirmed_route_keeps_last_confirmed_path(
    journal_runtime, monkeypatch, error,
):
    runtime, _ = journal_runtime
    runtime._prev_path["drone_1"] = "mesh"

    async def rejected_push(_client, _path_name, _drone_id):
        raise RuntimeError(error)

    monkeypatch.setattr(orchestrator, "_push_to_sdn", rejected_push)
    asyncio.run(runtime._run_step(SimpleNamespace()))
    envelopes, _ = asyncio.run(api_server._mission_events_after(0, 100))
    assert [envelope["phase"] for envelope in envelopes] == ["requested", "completed"]
    request, completed = envelopes
    assert request["last_confirmed_path"] == "mesh"
    assert request["decision"]["requested_path"] == "direct"
    assert completed["controller"] == {
        "status": "not_confirmed",
        "applied": False,
        "error": error,
    }
    assert completed["last_confirmed_path"] == "mesh"
    assert "confirmed_installed_path" not in completed
    assert completed["event"]["decision"]["installed_path"] == "mesh"


def test_completed_marker_and_run_row_rollback_together(journal_runtime):
    _, db_path = journal_runtime
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_completion BEFORE INSERT ON mission_event_log "
            "WHEN NEW.kind = 'decision_completed' "
            "BEGIN SELECT RAISE(ABORT, 'reject completion'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="reject completion"):
        orchestrator._log_experiments([{
            "run_id": "test-run", "step": 1, "drone_id": "drone_1",
            "event_json": "{}", "_controller_resolved_at": 1_800_000_001.0,
        }])
    assert _rows(db_path, "runs") == []
    assert _rows(db_path, "mission_event_log") == []


def test_mission_api_requires_auth_and_paginates_in_order(
    journal_runtime, monkeypatch,
):
    runtime, _ = journal_runtime

    async def acknowledged_push(_client, path_name, _drone_id):
        return {"status": "ok", "installed_path": path_name}

    monkeypatch.setattr(orchestrator, "_push_to_sdn", acknowledged_push)
    asyncio.run(runtime._run_step(SimpleNamespace()))
    client = TestClient(api_server.app)
    assert client.get("/mission/events").status_code == 401
    headers = {"Authorization": f"Bearer {_auth_token()}"}
    first = client.get("/mission/events", params={"limit": 1}, headers=headers)
    assert first.status_code == 200
    assert first.json()["has_more"] is True
    assert first.json()["events"][0]["phase"] == "requested"
    second = client.get(
        "/mission/events",
        params={"after_id": first.json()["next_cursor"], "limit": 1},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["has_more"] is False
    assert second.json()["events"][0]["phase"] == "completed"
    assert second.json()["next_cursor"] > first.json()["next_cursor"]
    assert client.get(
        "/mission/events", params={"limit": 101}, headers=headers,
    ).status_code == 422
    assert client.get(
        "/mission/events", params={"run_id": "another-run"}, headers=headers,
    ).json()["events"] == []


def test_mission_api_reports_journal_read_failure(journal_runtime, monkeypatch):
    async def unavailable_columns(_db, _table):
        raise sqlite3.OperationalError("simulated journal read failure")

    monkeypatch.setattr(api_server, "_table_columns", unavailable_columns)
    client = TestClient(api_server.app)
    response = client.get(
        "/mission/events",
        headers={"Authorization": f"Bearer {_auth_token()}"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Mission journal temporarily unavailable"}


def test_authenticated_websocket_resumes_from_cursor(
    journal_runtime, monkeypatch,
):
    runtime, _ = journal_runtime

    async def acknowledged_push(_client, path_name, _drone_id):
        return {"status": "ok", "installed_path": path_name}

    async def journal_only_broadcaster():
        while True:
            await api_server._broadcast_mission_events()
            await asyncio.sleep(0.01)

    monkeypatch.setattr(orchestrator, "_push_to_sdn", acknowledged_push)
    monkeypatch.setattr(api_server, "telemetry_broadcaster", journal_only_broadcaster)
    asyncio.run(runtime._run_step(SimpleNamespace()))
    token = _auth_token()

    with TestClient(api_server.app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "auth", "token": token, "cursor": 0})
            assert websocket.receive_json() == {"type": "auth", "status": "ok"}
            requested = websocket.receive_json()
            assert requested["phase"] == "requested"
            assert requested["controller"]["applied"] is None
            cursor = requested["cursor"]

        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "auth", "token": token, "cursor": cursor})
            assert websocket.receive_json() == {"type": "auth", "status": "ok"}
            completed = websocket.receive_json()
            assert completed["phase"] == "completed"
            assert completed["cursor"] > cursor
            assert completed["controller"]["status"] == "acknowledged"

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws") as websocket:
                websocket.send_json({"type": "auth", "token": token, "cursor": -1})
                websocket.receive_json()
        assert exc_info.value.code == 4401


def test_default_websocket_cursor_recovers_only_inflight_request(
    journal_runtime, monkeypatch,
):
    runtime, db_path = journal_runtime

    async def acknowledged_push(_client, path_name, _drone_id):
        return {"status": "ok", "installed_path": path_name}

    monkeypatch.setattr(orchestrator, "_push_to_sdn", acknowledged_push)
    asyncio.run(runtime._run_step(SimpleNamespace()))
    completed_cursor = asyncio.run(api_server._latest_completed_mission_cursor())
    event = DecisionEvent.model_validate_json(_rows(db_path, "runs")[0][-1])
    pending = RouteRequestStartedV1.model_validate({
        "schema_version": "route_request_started_v1",
        "event_id": f"{event.run_id}:99:{event.drone_id}:route_request",
        "transition_id": f"{event.run_id}:99:{event.drone_id}",
        "run_id": event.run_id,
        "step": 99,
        "drone_id": event.drone_id,
        "mode": event.mode,
        "occurred_at": event.timestamp + 1.0,
        "telemetry": event.telemetry,
        "inference": event.inference,
        "decision": {
            "source": event.decision.source,
            "policy_path": "satellite",
            "requested_path": "satellite",
            "threat_level": event.decision.threat_level,
            "safety_override": False,
            "safe_action_mask": event.decision.safe_action_mask,
            "no_safe_route": event.decision.no_safe_route,
            "constraint_reason": event.decision.constraint_reason,
            "safety_threshold": event.decision.safety_threshold,
            "network_action": "forward",
            "routing_contract": event.decision.routing_contract,
        },
        "last_confirmed_path": event.decision.installed_path,
        "configured_backend": "mock",
    })
    orchestrator._log_route_request_started(pending)
    # A later completed cycle for the same drone must not conceal an older
    # unresolved request when a fresh socket has no explicit resume cursor.
    asyncio.run(runtime._run_step(SimpleNamespace()))
    assert asyncio.run(api_server._latest_completed_mission_cursor()) == completed_cursor
    events, _ = asyncio.run(api_server._mission_events_after(completed_cursor, 100))
    assert [entry["phase"] for entry in events] == ["requested", "completed"]
    assert events[0]["transition_id"] == pending.transition_id

    async def journal_only_broadcaster():
        while True:
            await api_server._broadcast_mission_events()
            await asyncio.sleep(0.01)

    monkeypatch.setattr(api_server, "telemetry_broadcaster", journal_only_broadcaster)
    with TestClient(api_server.app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "auth", "token": _auth_token()})
            assert websocket.receive_json() == {"type": "auth", "status": "ok"}
            recovered = websocket.receive_json()
            assert recovered == events[0]
