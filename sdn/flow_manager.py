"""
sdn/flow_manager.py — [REAL]
Flow rule installer and failover logic for the Ryu SDN controller.

Provides:
  - install_flow(datapath, path_name)  — install OpenFlow rule on switch
  - select_safe_path(requested, available) — deterministic failover
  - REST endpoint /sdn/route consumed by the orchestrator

This module is imported by sdn/controller.py (Ryu REAL mode).
"""

import logging
from typing import Optional

from ryu.lib.packet import ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3

import yaml
from pathlib import Path

_BASE = Path(__file__).parent.parent
_SDN_CFG = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_PORT_MAP = _SDN_CFG["port_map"]
_PRIORITY_MAP = _SDN_CFG["flow_priority"]
_FAILOVER = _SDN_CFG["failover_priority"]

logger = logging.getLogger(__name__)

VALID_PATHS = {"direct", "satellite", "mesh", "fallback"}


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


def _send_flow(datapath, priority, match, actions) -> None:
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
    )
    datapath.send_msg(flow_mod)


def install_flow(datapath, path_name: str) -> None:
    """
    Install an OpenFlow 1.3 flow rule on the given datapath (switch)
    to route traffic via the specified path's output port.

    Args:
        datapath:   Ryu datapath object (OpenFlow switch connection)
        path_name:  One of 'direct', 'satellite', 'mesh', 'fallback'
    """
    dpid = datapath.id
    parser = datapath.ofproto_parser

    out_port = _PORT_MAP.get(path_name, _PORT_MAP["fallback"])
    priority = _PRIORITY_MAP.get(path_name, 100)

    if dpid == 1:
        # Ingress switch (s1)
        # Route from Drone (Port 4) -> Active Path out_port
        match_drone = parser.OFPMatch(in_port=4)
        actions_drone = [parser.OFPActionOutput(out_port)]
        _send_flow(datapath, priority, match_drone, actions_drone)
        
        # Static rules from paths back to Drone
        for p in [1, 2, 3]:
            match_back = parser.OFPMatch(in_port=p)
            actions_back = [parser.OFPActionOutput(4)]
            _send_flow(datapath, 50, match_back, actions_back)

    elif dpid == 5:
        # Egress switch (s5)
        # Route from Base Station (Port 4) -> Active Path out_port
        match_bs = parser.OFPMatch(in_port=4)
        actions_bs = [parser.OFPActionOutput(out_port)]
        _send_flow(datapath, priority, match_bs, actions_bs)
        
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
        "Dynamic flow installed: datapath=%s (s%d) | path=%s | port=%d | priority=%d",
        datapath.id, dpid, path_name, out_port, priority,
    )


def install_fallback_rule(datapath) -> None:
    """
    Install a table-miss (lowest priority) rule directing all unmatched
    traffic to the default port. Called at switch connection.
    """
    dpid = datapath.id
    parser = datapath.ofproto_parser

    if dpid == 1:
        # Default route from Drone (Port 4) to fallback port (mesh, Port 3)
        match = parser.OFPMatch(in_port=4)
        actions = [parser.OFPActionOutput(3)]
        _send_flow(datapath, 10, match, actions)
    elif dpid == 5:
        # Default route from Base Station (Port 4) to fallback port (mesh, Port 3)
        match = parser.OFPMatch(in_port=4)
        actions = [parser.OFPActionOutput(3)]
        _send_flow(datapath, 10, match, actions)
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
