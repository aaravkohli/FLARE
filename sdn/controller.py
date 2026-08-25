"""
sdn/controller.py — [REAL] [Requires Open vSwitch + Ryu]
Ryu SDN controller with OpenFlow 1.3 support.

Responsibilities:
  - Handle switch connections (install fallback table-miss rules)
  - Receive routing decisions from the orchestrator via REST API
  - Install flow rules dynamically using flow_manager.py
  - Expose /sdn/route POST endpoint (same API as mock_sdn.py)

Run with:
  ryu-manager sdn/controller.py --ofp-tcp-listen-port 6633

Then configure Open vSwitch to connect to this controller:
  ovs-vsctl set-controller br0 tcp:127.0.0.1:6633

NOTE: This module requires a real OpenFlow-capable switch.
      For simulation mode, use sdn/mock_sdn.py instead.
"""

import hmac
import logging
import os

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import dpid as dpid_lib
from ryu.ofproto import ofproto_v1_3
from webob import Response
import json

from sdn.flow_manager import install_fallback_rule, install_flow, select_safe_path
from sdn.route_contract import (
    VALID_DRONES,
    VALID_PATHS,
    action_id_for_path,
    normalize_installed_path,
    validate_route_action,
)

logger = logging.getLogger(__name__)

_KNOWN_DATAPATHS: dict = {}  # dpid → datapath object
_DEVELOPMENT_SDN_TOKEN = "antijam-development-sdn-token"
SDN_API_TOKEN = os.getenv("AJ_SDN_TOKEN", _DEVELOPMENT_SDN_TOKEN)
if os.getenv("AJ_ENV", "development").lower() in {"production", "prod"} and (
    SDN_API_TOKEN == _DEVELOPMENT_SDN_TOKEN or len(SDN_API_TOKEN) < 24
):
    raise RuntimeError(
        "AJ_SDN_TOKEN must be set to a unique value of at least 24 characters in production"
    )


class AntiJammingController(app_manager.RyuApp):
    """
    Ryu application for anti-jamming drone network routing.
    Exposes a REST API on port 8080 for RL agent decisions.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        wsgi = kwargs["wsgi"]
        wsgi.register(SDNRestController, {AntiJammingController.__name__: self})
        logger.info("AntiJammingController started. REST API ready on :8080")

    # ------------------------------------------------------------------
    # OpenFlow event handlers
    # ------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Called when a new switch connects. Install fallback table-miss rule."""
        datapath = ev.msg.datapath
        _KNOWN_DATAPATHS[datapath.id] = datapath
        logger.info("Switch connected: dpid=%s", dpid_lib.dpid_to_str(datapath.id))
        install_fallback_rule(datapath)

    @set_ev_cls(ofp_event.EventOFPStateChange, MAIN_DISPATCHER)
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            _KNOWN_DATAPATHS[datapath.id] = datapath
        elif datapath.id in _KNOWN_DATAPATHS:
            del _KNOWN_DATAPATHS[datapath.id]
            logger.warning("Switch disconnected: dpid=%s", dpid_lib.dpid_to_str(datapath.id))

    def apply_routing_decision(self, path_name: str, drone_id: str = "drone_1") -> dict:
        """
        Apply a routing decision to all connected switches.
        Called by the REST API endpoint.
        """
        available = list(_KNOWN_DATAPATHS.keys())
        if not available:
            logger.error("No switches connected! Cannot install flow rules.")
            return {"status": "error", "reason": "no_switches"}

        controller_path = select_safe_path(path_name)
        installed_path = normalize_installed_path(controller_path)

        for dpid, datapath in _KNOWN_DATAPATHS.items():
            install_flow(datapath, installed_path, drone_id)

        logger.info(
            "Routing applied: drone=%s | path=%s | switches=%d",
            drone_id,
            installed_path,
            len(_KNOWN_DATAPATHS),
        )
        return {
            "status": "ok",
            "installed_path": installed_path,
            "installed_action_id": action_id_for_path(installed_path),
            "switches_updated": len(_KNOWN_DATAPATHS),
        }


class SDNRestController(ControllerBase):
    """REST API surface for the Ryu controller."""

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.controller: AntiJammingController = data[AntiJammingController.__name__]

    @staticmethod
    def _authorized(req) -> bool:
        scheme, _, supplied_token = req.headers.get("Authorization", "").partition(" ")
        return (
            scheme.lower() == "bearer"
            and bool(supplied_token)
            and hmac.compare_digest(supplied_token, SDN_API_TOKEN)
        )

    @staticmethod
    def _unauthorized_response() -> Response:
        return Response(
            status=401,
            content_type="application/json",
            body=json.dumps({"error": "Invalid SDN service token"}).encode(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @route("sdn", "/sdn/route", methods=["POST"])
    def install_route(self, req, **kwargs):
        if not self._authorized(req):
            return self._unauthorized_response()
        try:
            body = json.loads(req.body)
        except json.JSONDecodeError:
            return Response(status=400, json={"error": "Invalid JSON"})

        path_name = body.get("path_name")
        drone_id = body.get("drone_id", "drone_1")
        action_id = body.get("action_id")
        if path_name not in VALID_PATHS or drone_id not in VALID_DRONES:
            return Response(
                status=422,
                content_type="application/json",
                body=json.dumps({"error": "Invalid path_name or drone_id"}).encode(),
            )
        try:
            validate_route_action(path_name, action_id)
        except ValueError as exc:
            return Response(
                status=422,
                content_type="application/json",
                body=json.dumps({"error": str(exc)}).encode(),
            )

        result = self.controller.apply_routing_decision(path_name, drone_id)
        return Response(
            status=200 if result.get("status") == "ok" else 503,
            content_type="application/json",
            body=json.dumps(result).encode(),
        )

    @route("sdn", "/sdn/flows", methods=["GET"])
    def get_flows(self, req, **kwargs):
        if not self._authorized(req):
            return self._unauthorized_response()
        switches = {str(k): "connected" for k in _KNOWN_DATAPATHS}
        return Response(
            content_type="application/json",
            body=json.dumps({"switches": switches}).encode(),
        )

    @route("sdn", "/health", methods=["GET"])
    def health(self, req, **kwargs):
        return Response(
            content_type="application/json",
            body=json.dumps({"status": "ok", "mode": "real", "switches": len(_KNOWN_DATAPATHS)}).encode(),
        )
