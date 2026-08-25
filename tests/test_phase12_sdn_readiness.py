"""Tests for fail-closed OpenFlow topology readiness and link availability."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

from sdn.flow_manager import select_safe_path
import sdn.mock_sdn as mock_sdn
from sdn.topology_state import TopologyState, port_descriptor_is_up


@pytest.fixture
def topology() -> TopologyState:
    config = yaml.safe_load(
        (mock_sdn._BASE / "config" / "sdn_config.yaml").read_text()
    )
    return TopologyState.from_config(config)


def _connect_complete_topology(topology: TopologyState) -> None:
    inventories = {
        1: {port: True for port in range(1, 7)},
        2: {1: True, 2: True},
        3: {1: True, 2: True},
        4: {1: True, 2: True},
        5: {port: True for port in range(1, 5)},
    }
    for dpid, ports in inventories.items():
        topology.connect(dpid)
        topology.set_port_inventory(dpid, ports)


def test_topology_is_not_ready_until_every_inventory_arrives(topology):
    for dpid in topology.expected_dpids:
        topology.connect(dpid)
    for dpid in topology.expected_dpids - {4}:
        topology.set_port_inventory(dpid, {1: True, 2: True, 3: True, 4: True})

    assert topology.ready is False
    snapshot = topology.snapshot()
    assert snapshot["connected_switches"] == [1, 2, 3, 4, 5]
    assert 4 not in snapshot["inventory_complete_switches"]


def test_complete_topology_exposes_all_routes_for_every_drone(topology):
    _connect_complete_topology(topology)

    assert topology.ready is True
    for drone_id in ("drone_1", "drone_2", "drone_3"):
        assert topology.available_paths(drone_id) == [
            "direct",
            "satellite",
            "mesh",
        ]


def test_path_port_failure_only_removes_the_affected_route(topology):
    _connect_complete_topology(topology)
    topology.set_port_state(1, 1, False)

    assert topology.available_paths("drone_1") == ["satellite", "mesh"]
    status = topology.route_status("direct", "drone_1")
    assert status.available is False
    assert "port_down:1:1" in status.reasons
    assert select_safe_path("direct", topology.available_paths("drone_1")) == "satellite"


def test_drone_access_failure_isolated_to_that_drone(topology):
    _connect_complete_topology(topology)
    topology.set_port_state(1, 5, False)

    assert topology.available_paths("drone_1") == ["direct", "satellite", "mesh"]
    assert topology.available_paths("drone_2") == []
    assert topology.available_paths("drone_3") == ["direct", "satellite", "mesh"]


def test_missing_port_is_not_assumed_up(topology):
    _connect_complete_topology(topology)
    topology.set_port_inventory(3, {1: True})

    status = topology.route_status("satellite", "drone_1")
    assert status.available is False
    assert "port_missing:3:2" in status.reasons


def test_switch_disconnect_clears_inventory_and_readiness(topology):
    _connect_complete_topology(topology)
    topology.disconnect(3)

    assert topology.ready is False
    assert "satellite" not in topology.available_paths("drone_1")
    status = topology.route_status("satellite", "drone_1")
    assert "switch_disconnected:3" in status.reasons


def test_unidentified_failed_handshake_is_safe_to_ignore(topology):
    topology.disconnect(None)
    assert topology.snapshot()["connected_switches"] == []


def test_switch_reconnect_requires_a_fresh_port_inventory(topology):
    _connect_complete_topology(topology)
    topology.connect(3, reset_inventory=True)

    assert topology.ready is False
    assert "port_inventory_pending:3" in topology.route_status(
        "satellite", "drone_1"
    ).reasons


@pytest.mark.parametrize(
    ("config", "state", "expected"),
    [(0, 0, True), (1, 0, False), (0, 2, False), (0, 4, False), (1, 2, False)],
)
def test_openflow_port_flags_are_interpreted_fail_closed(config, state, expected):
    descriptor = SimpleNamespace(config=config, state=state)
    assert port_descriptor_is_up(
        descriptor,
        port_down_mask=1,
        unusable_state_mask=2 | 4,
    ) is expected


def test_external_route_api_rejects_controller_only_fallback_label():
    client = TestClient(mock_sdn.app)
    response = client.post(
        "/sdn/route",
        json={"path_name": "fallback", "action_id": 2, "drone_id": "drone_1"},
        headers={"Authorization": f"Bearer {mock_sdn.SDN_API_TOKEN}"},
    )
    assert response.status_code == 422
