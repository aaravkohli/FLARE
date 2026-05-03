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


def install_flow(datapath, path_name: str) -> None:
    """
    Install an OpenFlow 1.3 flow rule on the given datapath (switch)
    to route traffic via the specified path's output port.

    Args:
        datapath:   Ryu datapath object (OpenFlow switch connection)
        path_name:  One of 'direct', 'satellite', 'mesh', 'fallback'
    """
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser

    out_port = _PORT_MAP.get(path_name, _PORT_MAP["fallback"])
    priority = _PRIORITY_MAP.get(path_name, 10)

    # Match all IPv4 traffic (simplified; production would match on src/dst)
    match = parser.OFPMatch(eth_type=0x0800)

    # Action: forward to the port assigned to this path
    actions = [parser.OFPActionOutput(out_port)]

    # Build and install the flow mod (hard timeout = 0 → permanent until overwritten)
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
    logger.info(
        "Flow installed: datapath=%s | path=%s | port=%d | priority=%d",
        datapath.id, path_name, out_port, priority,
    )


def install_fallback_rule(datapath) -> None:
    """
    Install a table-miss (lowest priority) rule directing all unmatched
    traffic to the mesh (fallback) port. Called at switch connection.
    """
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    fallback_port = _PORT_MAP["fallback"]

    match = parser.OFPMatch()  # match-all
    actions = [parser.OFPActionOutput(fallback_port)]
    inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
    flow_mod = parser.OFPFlowMod(
        datapath=datapath,
        priority=_PRIORITY_MAP["fallback"],
        match=match,
        instructions=inst,
    )
    datapath.send_msg(flow_mod)
    logger.info(
        "Fallback table-miss rule installed: datapath=%s → port=%d",
        datapath.id, fallback_port,
    )
