"""Regression tests for the orchestrator-to-OpenFlow route contract."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
import pytest

import orchestrator.loop as orchestrator
import fl.client as fl_client
from sdn import flow_manager
import sdn.mock_sdn as mock_sdn
from sdn.route_contract import (
    action_id_for_path,
    normalize_installed_path,
    validate_route_action,
)


AUTH = {"Authorization": f"Bearer {mock_sdn.SDN_API_TOKEN}"}


class FakeOfproto:
    OFPIT_APPLY_ACTIONS = 4
    OFPFC_ADD = 0
    OFPFC_DELETE_STRICT = 4
    OFPFF_SEND_FLOW_REM = 1
    OFPP_ANY = 0xFFFFFFFF
    OFPG_ANY = 0xFFFFFFFF


class FakeParser:
    @staticmethod
    def OFPMatch(**fields):
        return {"type": "match", **fields}

    @staticmethod
    def OFPActionOutput(port):
        return {"type": "output", "port": port}

    @staticmethod
    def OFPInstructionActions(instruction_type, actions):
        return {
            "type": "instruction",
            "instruction_type": instruction_type,
            "actions": actions,
        }

    @staticmethod
    def OFPFlowMod(**fields):
        return {"type": "flow_mod", **fields}


class FakeDatapath:
    def __init__(self, dpid: int):
        self.id = dpid
        self.ofproto = FakeOfproto()
        self.ofproto_parser = FakeParser()
        self.messages: list[dict] = []

    def send_msg(self, message):
        self.messages.append(message)


def _output_port(flow_mod: dict) -> int:
    return flow_mod["instructions"][0]["actions"][0]["port"]


@pytest.mark.parametrize(
    ("path_name", "action_id"),
    [("direct", 0), ("satellite", 1), ("mesh", 2), ("fallback", 2)],
)
def test_route_contract_has_one_action_for_each_path(path_name, action_id):
    assert action_id_for_path(path_name) == action_id
    validate_route_action(path_name, action_id)


def test_route_contract_rejects_mismatched_and_boolean_actions():
    with pytest.raises(ValueError, match="does not match"):
        validate_route_action("satellite", 0)
    with pytest.raises(ValueError, match="must be an integer"):
        validate_route_action("direct", True)


def test_fallback_is_reported_as_the_physical_mesh_route():
    assert normalize_installed_path("fallback") == "mesh"


def test_orchestrator_sends_the_action_matching_the_requested_path():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok", "installed_path": "satellite"}

    class FakeClient:
        payload = None

        async def post(self, _url, *, json, headers, timeout):
            self.payload = json
            return FakeResponse()

    client = FakeClient()
    result = asyncio.run(
        orchestrator._push_to_sdn(client, "satellite", "drone_2")
    )

    assert client.payload == {
        "path_name": "satellite",
        "drone_id": "drone_2",
        "action_id": 1,
    }
    assert result["installed_action_id"] == 1


def test_orchestrator_rejects_an_inconsistent_controller_response():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok",
                "installed_path": "mesh",
                "installed_action_id": 0,
            }

    class FakeClient:
        async def post(self, _url, *, json, headers, timeout):
            return FakeResponse()

    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(orchestrator._push_to_sdn(FakeClient(), "mesh", "drone_1"))


def test_mock_controller_rejects_a_mismatched_path_and_action():
    client = TestClient(mock_sdn.app)
    response = client.post(
        "/sdn/route",
        json={"path_name": "satellite", "action_id": 0, "drone_id": "drone_1"},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_mock_controller_keeps_independent_per_drone_routes(monkeypatch):
    monkeypatch.setattr(mock_sdn, "_flow_table", {})
    monkeypatch.setattr(
        mock_sdn,
        "_available_paths",
        {"direct", "satellite", "mesh"},
    )
    client = TestClient(mock_sdn.app)

    direct = client.post(
        "/sdn/route",
        json={"path_name": "direct", "action_id": 0, "drone_id": "drone_1"},
        headers=AUTH,
    )
    mesh = client.post(
        "/sdn/route",
        json={"path_name": "mesh", "action_id": 2, "drone_id": "drone_2"},
        headers=AUTH,
    )

    assert direct.status_code == mesh.status_code == 200
    assert mock_sdn._flow_table["drone_1"]["path"] == "direct"
    assert mock_sdn._flow_table["drone_2"]["path"] == "mesh"


def test_real_flow_replaces_legacy_priorities_and_matches_one_drone():
    datapath = FakeDatapath(1)
    flow_manager.install_flow(datapath, "satellite", "drone_2")

    deletes = [
        message
        for message in datapath.messages
        if message["command"] == FakeOfproto.OFPFC_DELETE_STRICT
    ]
    assert {message["priority"] for message in deletes} == {80, 90, 100}
    per_drone_deletes = [
        message for message in deletes if "eth_src" in message["match"]
    ]
    legacy_deletes = [
        message for message in deletes if message["match"].get("in_port") == 4
    ]
    assert all(
        message["match"] == {
            "type": "match",
            "eth_src": "00:00:00:00:00:03",
        }
        for message in per_drone_deletes
    )
    assert {message["priority"] for message in per_drone_deletes} == {80, 90, 100}
    assert {message["priority"] for message in legacy_deletes} == {80, 90, 100}

    additions = [
        message
        for message in datapath.messages
        if message["command"] == FakeOfproto.OFPFC_ADD
    ]
    outbound = next(
        message
        for message in additions
        if message["match"].get("eth_src") == "00:00:00:00:00:03"
    )
    return_delivery = next(
        message
        for message in additions
        if message["match"].get("eth_dst") == "00:00:00:00:00:03"
    )
    assert outbound["priority"] == 100
    assert _output_port(outbound) == 2
    assert _output_port(return_delivery) == 5


def test_real_flow_rejects_unknown_drone_before_sending_messages():
    datapath = FakeDatapath(1)
    with pytest.raises(ValueError, match="Unknown drone_id"):
        flow_manager.install_flow(datapath, "direct", "drone_99")
    assert datapath.messages == []


def test_fallback_rules_cover_every_drone_access_port():
    datapath = FakeDatapath(1)
    flow_manager.install_fallback_rule(datapath)

    destination_rules = {
        message["match"]["eth_dst"]: _output_port(message)
        for message in datapath.messages
        if "eth_dst" in message["match"]
    }
    assert destination_rules == {
        "00:00:00:00:00:02": 4,
        "00:00:00:00:00:03": 5,
        "00:00:00:00:00:04": 6,
    }


def test_egress_fallback_delivers_return_traffic_to_the_base_station():
    datapath = FakeDatapath(5)
    flow_manager.install_fallback_rule(datapath)

    path_return_rules = {
        message["match"]["in_port"]: _output_port(message)
        for message in datapath.messages
        if "in_port" in message["match"]
    }
    assert path_return_rules == {1: 4, 2: 4, 3: 4}


def test_required_real_fl_data_never_falls_back_to_synthetic(tmp_path, monkeypatch):
    monkeypatch.setattr(fl_client, "_PROC", tmp_path)

    def unexpected_fallback(**_kwargs):
        raise AssertionError("synthetic fallback must not run in required-real mode")

    monkeypatch.setattr(fl_client, "load_local_data", unexpected_fallback)

    with pytest.raises(FileNotFoundError, match="Required real dataset"):
        fl_client.load_real_data(
            "drone_1",
            seq_len=10,
            allow_synthetic_fallback=False,
        )


def test_production_fl_clients_mount_and_require_real_data():
    compose_path = orchestrator._BASE / "docker-compose.prod.yml"
    compose = orchestrator.yaml.safe_load(compose_path.read_text())

    for client_name in ("fl-client-1", "fl-client-2", "fl-client-3"):
        service = compose["services"][client_name]
        assert "--real" in service["command"]
        assert "--require-real-data" in service["command"]
        assert any(
            volume.endswith(":/app/datasets/processed:ro")
            for volume in service["volumes"]
        )
