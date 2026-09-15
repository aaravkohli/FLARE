"""
sdn/flow_manager.py — [REAL]
Flow rule installer and failover logic for the Ryu SDN controller.

Provides:
  - install_flow(datapath, path_name, drone_id) — install per-drone rules
  - select_safe_path(requested, available) — deterministic failover
  - REST endpoint /sdn/route consumed by the orchestrator

This module is imported by sdn/controller.py (Ryu REAL mode).
"""

from __future__ import annotations

import logging
from typing import Optional

import yaml
from pathlib import Path

from fleet.registry import get_drone, list_drones
from sdn.route_contract import VALID_PATHS

_BASE = Path(__file__).parent.parent
_SDN_CFG = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_PORT_MAP = _SDN_CFG["port_map"]
_PRIORITY_MAP = _SDN_CFG["flow_priority"]
_FAILOVER = _SDN_CFG["failover_priority"]

_DYNAMIC_PRIORITIES = sorted(
    {
        int(_PRIORITY_MAP[path])
        for path in ("direct", "satellite", "mesh")
    }
)
_ROUTE_PRIORITY = max(_DYNAMIC_PRIORITIES)
_ACCESS_PRIORITY = _ROUTE_PRIORITY + 10
_ROUTE_COOKIE_BASE = 0xF1A00000
_CONTAINMENT_COOKIE_BASE = 0xF1B00000
_CONTAINMENT_ALLOW_PRIORITY = int(_PRIORITY_MAP.get("containment_allow", 300))
_CONTAINMENT_DROP_PRIORITY = int(_PRIORITY_MAP.get("containment_drop", 290))
_CONTROL_UDP_PORTS = tuple(
    int(value) for value in _SDN_CFG.get("controller", {}).get("control_udp_ports", [9000])
)

logger = logging.getLogger(__name__)

def select_safe_path(requested: str, available: Optional[list] = None) -> str:
    """
    Deterministic failover: walk failover_priority until an available path found.

    Args:
        requested:  The path the RL agent wants to use.
        available:  List of currently available path names (None = all available).

    Returns:
        The safest available path name.
    """
    if available is None:
        available = list(VALID_PATHS)
    available_set = set(available)

    if requested in available_set:
        return requested

    for fallback in _FAILOVER:
        if fallback in available_set or fallback == "fallback":
            logger.warning(
                "Path '%s' unavailable. Failing over to '%s'.", requested, fallback
            )
            return fallback

    return "fallback"


def _send_flow(datapath, priority, match, actions, *, cookie: int = 0) -> None:
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
    flow_mod = parser.OFPFlowMod(
        datapath=datapath,
        priority=priority,
        match=match,
        instructions=inst,
        command=ofproto.OFPFC_ADD,
        idle_timeout=0,
        hard_timeout=0,
        flags=ofproto.OFPFF_SEND_FLOW_REM,
        cookie=cookie,
    )
    datapath.send_msg(flow_mod)


def _delete_old_route_rules(datapath, match) -> None:
    """Remove current and legacy dynamic rules while preserving fallback rules."""
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    for priority in _DYNAMIC_PRIORITIES:
        flow_mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE_STRICT,
            priority=priority,
            match=match,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
        )
        datapath.send_msg(flow_mod)


def _delete_containment_rule(datapath, match, priority: int) -> None:
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    datapath.send_msg(parser.OFPFlowMod(
        datapath=datapath,
        command=ofproto.OFPFC_DELETE_STRICT,
        priority=priority,
        match=match,
        out_port=ofproto.OFPP_ANY,
        out_group=ofproto.OFPG_ANY,
    ))


def clear_containment(datapath, drone_id: str) -> None:
    """Remove all FLARE containment rules for one drone."""
    parser = datapath.ofproto_parser
    drone_mac = _drone_config(drone_id)["mac"]
    matches = [parser.OFPMatch(eth_src=drone_mac), parser.OFPMatch(eth_dst=drone_mac)]
    for match in matches:
        _delete_containment_rule(datapath, match, _CONTAINMENT_DROP_PRIORITY)
    for port in _CONTROL_UDP_PORTS:
        _delete_containment_rule(
            datapath,
            parser.OFPMatch(eth_type=0x0800, ip_proto=17, eth_src=drone_mac, udp_dst=port),
            _CONTAINMENT_ALLOW_PRIORITY,
        )


def install_hold_flow(datapath, drone_id: str) -> None:
    """Fail closed for one drone by installing explicit empty-action drops."""
    clear_containment(datapath, drone_id)
    parser = datapath.ofproto_parser
    drone = _drone_config(drone_id)
    cookie = _CONTAINMENT_COOKIE_BASE + int(drone["index"])
    if datapath.id == 1:
        _send_flow(
            datapath, _CONTAINMENT_DROP_PRIORITY,
            parser.OFPMatch(eth_src=drone["mac"]), [], cookie=cookie,
        )
    elif datapath.id == 5:
        _send_flow(
            datapath, _CONTAINMENT_DROP_PRIORITY,
            parser.OFPMatch(eth_dst=drone["mac"]), [], cookie=cookie,
        )


def install_containment(
    datapath, drone_id: str, mode: str, *, control_path: str | None = None
) -> None:
    """Install NORMAL, CONTROL_ONLY/RESTRICTED, or QUARANTINED enforcement."""
    if mode not in {"normal", "restricted", "control_only", "quarantined"}:
        raise ValueError(f"unknown containment mode: {mode!r}")
    if mode in {"restricted", "control_only"} and control_path not in {
        "direct", "satellite", "mesh"
    }:
        raise ValueError("control-only containment requires an installed forwarding route")
    clear_containment(datapath, drone_id)
    if mode == "normal":
        return
    if mode == "quarantined":
        install_hold_flow(datapath, drone_id)
        return
    if datapath.id != 1:
        return
    parser = datapath.ofproto_parser
    drone = _drone_config(drone_id)
    cookie = _CONTAINMENT_COOKIE_BASE + int(drone["index"])
    # Control packets must follow the currently installed explicit ingress
    # route. OFPP_NORMAL depends on OVS learning state and is not a reliable
    # forwarding path in this controlled OpenFlow topology.
    control_output = parser.OFPActionOutput(int(_PORT_MAP[control_path]))
    for port in _CONTROL_UDP_PORTS:
        _send_flow(
            datapath,
            _CONTAINMENT_ALLOW_PRIORITY,
            parser.OFPMatch(
                eth_type=0x0800, ip_proto=17, eth_src=drone["mac"], udp_dst=port
            ),
            [control_output],
            cookie=cookie,
        )
    _send_flow(
        datapath,
        _CONTAINMENT_DROP_PRIORITY,
        parser.OFPMatch(eth_src=drone["mac"]),
        [],
        cookie=cookie,
    )


def _drone_config(drone_id: str) -> dict:
    drone = get_drone(drone_id, enabled_only=True)
    if drone is None:
        raise ValueError(f"Unknown drone_id or disabled identity: {drone_id!r}")
    return drone


def install_flow(datapath, path_name: str, drone_id: str = "drone_1") -> None:
    """
    Install an OpenFlow 1.3 flow rule on the given datapath (switch)
    to route traffic via the specified path's output port.

    Args:
        datapath:   Ryu datapath object (OpenFlow switch connection)
        path_name:  One of 'direct', 'satellite', 'mesh', 'fallback'
        drone_id:   Drone whose Ethernet traffic should use the path
    """
    if path_name not in VALID_PATHS:
        raise ValueError(f"Unknown path_name: {path_name!r}")

    dpid = datapath.id
    parser = datapath.ofproto_parser

    out_port = _PORT_MAP.get(path_name, _PORT_MAP["fallback"])
    priority = _ROUTE_PRIORITY
    drone = _drone_config(drone_id)
    drone_mac = drone["mac"]
    access_port = int(drone["access_port"])
    cookie = _ROUTE_COOKIE_BASE + int(drone["index"])

    if dpid == 1:
        # Select the outbound path independently for each drone.
        match_drone = parser.OFPMatch(in_port=access_port, eth_src=drone_mac)
        _delete_old_route_rules(datapath, match_drone)
        # Clean rules installed by the former single-drone implementation.
        _delete_old_route_rules(datapath, parser.OFPMatch(in_port=4))
        actions_drone = [parser.OFPActionOutput(out_port)]
        _send_flow(
            datapath,
            priority,
            match_drone,
            actions_drone,
            cookie=cookie,
        )

        # Return traffic is delivered to the correct drone access port.
        match_back = parser.OFPMatch(eth_dst=drone_mac)
        actions_back = [parser.OFPActionOutput(access_port)]
        _send_flow(datapath, _ACCESS_PRIORITY, match_back, actions_back)

    elif dpid == 5:
        # Base-station traffic is routed according to the destination drone.
        match_bs = parser.OFPMatch(eth_dst=drone_mac)
        _delete_old_route_rules(datapath, match_bs)
        # Clean rules installed by the former single-drone implementation.
        _delete_old_route_rules(datapath, parser.OFPMatch(in_port=4))
        actions_bs = [parser.OFPActionOutput(out_port)]
        _send_flow(datapath, priority, match_bs, actions_bs, cookie=cookie)
        
        # Static rules from paths back to Base Station
        for p in [1, 2, 3]:
            match_back = parser.OFPMatch(in_port=p)
            actions_back = [parser.OFPActionOutput(4)]
            _send_flow(datapath, 50, match_back, actions_back)

    elif dpid in [2, 3, 4]:
        # Pipeline transit switches (s2, s3, s4)
        # Route Port 1 <-> Port 2 bidirectionally
        match_1 = parser.OFPMatch(in_port=1)
        actions_1 = [parser.OFPActionOutput(2)]
        _send_flow(datapath, 100, match_1, actions_1)

        match_2 = parser.OFPMatch(in_port=2)
        actions_2 = [parser.OFPActionOutput(1)]
        _send_flow(datapath, 100, match_2, actions_2)

    logger.info(
        "Dynamic flow installed: datapath=%s (s%d) | drone=%s | path=%s | port=%d | priority=%d",
        datapath.id, dpid, drone_id, path_name, out_port, priority,
    )


def install_fallback_rule(datapath) -> None:
    """
    Install a table-miss (lowest priority) rule directing all unmatched
    traffic to the default port. Called at switch connection.
    """
    dpid = datapath.id
    parser = datapath.ofproto_parser

    if dpid == 1:
        for drone in list_drones(enabled_only=True):
            match_out = parser.OFPMatch(
                in_port=int(drone["access_port"]), eth_src=drone["mac"]
            )
            _send_flow(datapath, 10, match_out, [parser.OFPActionOutput(3)])
            match_back = parser.OFPMatch(eth_dst=drone["mac"])
            _send_flow(
                datapath,
                10,
                match_back,
                [parser.OFPActionOutput(int(drone["access_port"]))],
            )
        # Unknown or identity-mismatched access traffic is sent only to the
        # authenticated controller for source/port evidence and is not
        # forwarded by this table-miss rule.
        _send_flow(
            datapath,
            0,
            parser.OFPMatch(),
            [parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER)],
        )
    elif dpid == 5:
        for drone in list_drones(enabled_only=True):
            match = parser.OFPMatch(eth_dst=drone["mac"])
            actions = [parser.OFPActionOutput(3)]
            _send_flow(datapath, 10, match, actions)
        for path_port in [1, 2, 3]:
            match_back = parser.OFPMatch(in_port=path_port)
            actions_back = [parser.OFPActionOutput(4)]
            _send_flow(datapath, 10, match_back, actions_back)
    elif dpid in [2, 3, 4]:
        # Pipeline transit switches default pipe
        match_1 = parser.OFPMatch(in_port=1)
        actions_1 = [parser.OFPActionOutput(2)]
        _send_flow(datapath, 10, match_1, actions_1)

        match_2 = parser.OFPMatch(in_port=2)
        actions_2 = [parser.OFPActionOutput(1)]
        _send_flow(datapath, 10, match_2, actions_2)

    logger.info(
        "Fallback table-miss rule installed: datapath=%s (s%d)",
        datapath.id, dpid,
    )
