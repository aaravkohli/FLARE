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
import json
import logging
import os
from pathlib import Path
import time

import yaml
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    DEAD_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import dpid as dpid_lib, hub
from ryu.lib.packet import ethernet, packet
from ryu.ofproto import ofproto_v1_3
from webob import Response

from sdn.flow_manager import (
    clear_containment,
    install_containment,
    install_fallback_rule,
    install_flow,
    install_hold_flow,
    select_safe_path,
)
from sdn.route_contract import (
    HOLD_PATH,
    ROUTE_COMMANDS,
    ROUTABLE_PATHS,
    action_id_for_path,
    normalize_installed_path,
    validate_route_action,
)
from fleet.registry import is_active_drone
from fleet.registry import list_drones
from sdn.evidence import ControllerEvidenceStore
from sdn.topology_state import TopologyState, port_descriptor_is_up

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_SDN_CFG = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_BARRIER_TIMEOUT = float(
    _SDN_CFG.get("controller", {}).get("barrier_timeout_s", 1.0)
)

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
    Ryu application for FLARE communication-recovery routing.
    Exposes a REST API on port 8080 for RL agent decisions.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._topology = TopologyState.from_config(_SDN_CFG)
        self._barrier_waiters: dict[tuple[int, int], object] = {}
        self._port_inventory_parts: dict[int, dict[int, bool]] = {}
        self._installed_routes: dict[str, str] = {}
        self._containment: dict[str, dict] = {}
        self._evidence = ControllerEvidenceStore(
            source="ryu_openflow", independent=True
        )
        wsgi = kwargs["wsgi"]
        wsgi.register(SDNRestController, {AntiJammingController.__name__: self})
        logger.info("AntiJammingController started. REST API ready on :8080")
        self._evidence_monitor = hub.spawn(self._monitor_evidence)

    def _monitor_evidence(self):
        while True:
            for datapath in list(_KNOWN_DATAPATHS.values()):
                try:
                    datapath.send_msg(
                        datapath.ofproto_parser.OFPFlowStatsRequest(datapath)
                    )
                    if datapath.id == self._topology.ingress_dpid:
                        datapath.send_msg(
                            datapath.ofproto_parser.OFPPortStatsRequest(
                                datapath, 0, datapath.ofproto.OFPP_ANY
                            )
                        )
                except Exception:
                    logger.exception("Failed to request OpenFlow evidence statistics")
            hub.sleep(1.0)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_statistics_handler(self, ev):
        """Observe receive errors/drops on registry-bound ingress access ports."""
        if ev.msg.datapath.id != self._topology.ingress_dpid:
            return
        by_port = {
            int(drone["access_port"]): drone["drone_id"]
            for drone in list_drones(enabled_only=True)
        }
        now = time.time()
        for statistic in ev.msg.body:
            drone_id = by_port.get(int(statistic.port_no))
            if drone_id is None:
                continue
            self._evidence.update_port_observation(
                drone_id,
                received_packets=int(statistic.rx_packets),
                dropped_packets=int(statistic.rx_dropped),
                error_packets=int(statistic.rx_errors),
                timestamp=now,
            )

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_statistics_handler(self, ev):
        if ev.msg.datapath.id != self._topology.ingress_dpid:
            return
        observations = {
            drone["drone_id"]: {
                "mac": drone["mac"], "received": 0, "forwarded": 0, "dropped": 0
            }
            for drone in list_drones(enabled_only=True)
        }
        by_mac = {value["mac"]: key for key, value in observations.items()}
        for statistic in ev.msg.body:
            source_mac = statistic.match.get("eth_src")
            drone_id = by_mac.get(str(source_mac).lower()) if source_mac else None
            if drone_id is None:
                continue
            packet_count = max(0, int(getattr(statistic, "packet_count", 0)))
            has_output = any(
                getattr(instruction, "actions", None)
                for instruction in getattr(statistic, "instructions", [])
            )
            observations[drone_id]["received"] += packet_count
            if has_output:
                observations[drone_id]["forwarded"] += packet_count
            else:
                observations[drone_id]["dropped"] += packet_count
        now = time.time()
        for drone_id, observation in observations.items():
            if observation["received"]:
                self._evidence.update_flow_observation(
                    drone_id,
                    received_packets=observation["received"],
                    forwarded_packets=observation["forwarded"],
                    dropped_packets=observation["dropped"],
                    observed_source_mac=observation["mac"],
                    timestamp=now,
                )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Bind an observed Ethernet source to its physical ingress identity."""
        datapath = ev.msg.datapath
        if datapath.id != self._topology.ingress_dpid:
            return
        ingress_port = ev.msg.match.get("in_port")
        drone = next(
            (
                row for row in list_drones(enabled_only=True)
                if int(row["access_port"]) == ingress_port
            ),
            None,
        )
        if drone is None:
            return
        frame = packet.Packet(ev.msg.data).get_protocol(ethernet.ethernet)
        if frame is None:
            return
        # Only packets emitted from a participant-bound access port count as
        # participant-originated control-plane load. FLARE's own REST route
        # and containment calls must never become DoS evidence.
        self._evidence.record_control_message(
            drone["drone_id"], timestamp=time.time()
        )
        self._evidence.update_source_identity(
            drone["drone_id"], frame.src, timestamp=time.time()
        )

    # ------------------------------------------------------------------
    # OpenFlow event handlers
    # ------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Called when a new switch connects. Install fallback table-miss rule."""
        datapath = ev.msg.datapath
        _KNOWN_DATAPATHS[datapath.id] = datapath
        self._topology.connect(datapath.id, reset_inventory=True)
        self._port_inventory_parts.pop(datapath.id, None)
        logger.info("Switch connected: dpid=%s", dpid_lib.dpid_to_str(datapath.id))
        install_fallback_rule(datapath)
        datapath.send_msg(datapath.ofproto_parser.OFPPortDescStatsRequest(datapath, 0))

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def state_change_handler(self, ev):
        datapath = ev.datapath
        # Failed/aborted handshakes can enter DEAD before OpenFlow assigns a DPID.
        if datapath.id is None:
            return
        if ev.state == MAIN_DISPATCHER:
            _KNOWN_DATAPATHS[datapath.id] = datapath
            self._topology.connect(datapath.id)
        elif ev.state == DEAD_DISPATCHER:
            _KNOWN_DATAPATHS.pop(datapath.id, None)
            self._topology.disconnect(datapath.id)
            self._port_inventory_parts.pop(datapath.id, None)
            self._installed_routes.clear()
            logger.warning("Switch disconnected: dpid=%s", dpid_lib.dpid_to_str(datapath.id))

    @staticmethod
    def _descriptor_up(datapath, descriptor) -> bool:
        return port_descriptor_is_up(
            descriptor,
            port_down_mask=datapath.ofproto.OFPPC_PORT_DOWN,
            unusable_state_mask=(
                datapath.ofproto.OFPPS_LINK_DOWN
                | getattr(datapath.ofproto, "OFPPS_BLOCKED", 0)
            ),
        )

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_description_handler(self, ev):
        datapath = ev.msg.datapath
        inventory = self._port_inventory_parts.setdefault(datapath.id, {})
        inventory.update({
            port.port_no: self._descriptor_up(datapath, port)
            for port in ev.msg.body
        })
        if ev.msg.flags & datapath.ofproto.OFPMPF_REPLY_MORE:
            return
        inventory = self._port_inventory_parts.pop(datapath.id)
        self._topology.set_port_inventory(datapath.id, inventory)
        logger.info(
            "Port inventory ready: dpid=%s | ports=%d",
            dpid_lib.dpid_to_str(datapath.id),
            len(inventory),
        )

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        datapath = ev.msg.datapath
        descriptor = ev.msg.desc
        is_deleted = ev.msg.reason == datapath.ofproto.OFPPR_DELETE
        is_up = False if is_deleted else self._descriptor_up(datapath, descriptor)
        self._topology.set_port_state(datapath.id, descriptor.port_no, is_up)
        logger.warning(
            "Port state changed: dpid=%s | port=%s | up=%s",
            dpid_lib.dpid_to_str(datapath.id),
            descriptor.port_no,
            is_up,
        )

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        key = (ev.msg.datapath.id, ev.msg.xid)
        waiter = self._barrier_waiters.get(key)
        if waiter is not None:
            waiter.set()

    def _wait_for_barriers(self, datapaths: list) -> tuple[list[int], list[int]]:
        pending = []
        for datapath in datapaths:
            request = datapath.ofproto_parser.OFPBarrierRequest(datapath)
            datapath.set_xid(request)
            waiter = hub.Event()
            key = (datapath.id, request.xid)
            self._barrier_waiters[key] = waiter
            datapath.send_msg(request)
            pending.append((datapath, key, waiter))

        acknowledged: list[int] = []
        timed_out: list[int] = []
        for datapath, key, waiter in pending:
            received = waiter.wait(_BARRIER_TIMEOUT)
            self._barrier_waiters.pop(key, None)
            if not received:
                timed_out.append(datapath.id)
            else:
                acknowledged.append(datapath.id)
        return acknowledged, timed_out

    def apply_routing_decision(self, path_name: str, drone_id: str = "drone_1") -> dict:
        """
        Apply a routing decision to all connected switches.
        Called by the REST API endpoint.
        """
        topology = self._topology.snapshot()
        if not self._topology.ready:
            logger.error("OpenFlow topology is not ready; refusing route update.")
            return {
                "status": "error",
                "reason": "topology_not_ready",
                "topology": topology,
            }

        datapaths = [
            _KNOWN_DATAPATHS[dpid]
            for dpid in sorted(self._topology.expected_dpids)
        ]
        if path_name == HOLD_PATH:
            try:
                for datapath in datapaths:
                    install_hold_flow(datapath, drone_id)
            except Exception as exc:
                logger.exception("Hold-rule installation failed for %s", drone_id)
                return {"status": "error", "reason": "hold_installation_failed", "error": str(exc)}
            acknowledged, timed_out = self._wait_for_barriers(datapaths)
            if timed_out:
                return {
                    "status": "uncertain",
                    "reason": "barrier_timeout",
                    "installed_path": HOLD_PATH,
                    "installed_action_id": 3,
                    "acknowledged_switches": acknowledged,
                    "unacknowledged_switches": timed_out,
                }
            self._installed_routes[drone_id] = HOLD_PATH
            return {
                "status": "ok",
                "installed_path": HOLD_PATH,
                "installed_action_id": 3,
                "requested_path": HOLD_PATH,
                "failover_applied": False,
                "switches_updated": len(datapaths),
                "acknowledged_switches": acknowledged,
                "enforcement": "drop_data_plane",
            }

        available_paths = self._topology.available_paths(drone_id)
        if not available_paths:
            logger.error("No usable route remains for %s.", drone_id)
            return {
                "status": "error",
                "reason": "no_available_paths",
                "topology": topology,
            }

        controller_path = select_safe_path(path_name, available_paths)
        installed_path = normalize_installed_path(controller_path)

        try:
            for datapath in datapaths:
                # A route-level HOLD is reversible. Preserve independently
                # installed containment, but clear a prior HOLD rule when the
                # participant has no active restrictive containment policy.
                containment_mode = self._containment.get(drone_id, {}).get("mode", "normal")
                if containment_mode == "normal":
                    clear_containment(datapath, drone_id)
                install_flow(datapath, installed_path, drone_id)
                if containment_mode in {"restricted", "control_only"}:
                    install_containment(
                        datapath,
                        drone_id,
                        containment_mode,
                        control_path=installed_path,
                    )
        except Exception as exc:
            logger.exception("Flow installation failed for %s", drone_id)
            return {
                "status": "error",
                "reason": "flow_installation_failed",
                "error": str(exc),
                "topology": topology,
            }

        acknowledged, timed_out = self._wait_for_barriers(datapaths)
        if timed_out:
            logger.error("OpenFlow barrier timed out for switches %s", timed_out)
            return {
                "status": "uncertain",
                "reason": "barrier_timeout",
                "installed_path": installed_path,
                "installed_action_id": action_id_for_path(installed_path),
                "acknowledged_switches": acknowledged,
                "unacknowledged_switches": timed_out,
                "topology": self._topology.snapshot(),
            }

        self._installed_routes[drone_id] = installed_path

        logger.info(
            "Routing applied: drone=%s | path=%s | switches=%d",
            drone_id,
            installed_path,
            len(datapaths),
        )
        return {
            "status": "ok",
            "installed_path": installed_path,
            "installed_action_id": action_id_for_path(installed_path),
            "requested_path": path_name,
            "failover_applied": installed_path != path_name,
            "available_paths": available_paths,
            "switches_updated": len(datapaths),
            "acknowledged_switches": acknowledged,
        }

    def apply_containment(self, drone_id: str, mode: str, reason: str) -> dict:
        if not self._topology.ready:
            return {"status": "error", "reason": "topology_not_ready"}
        control_path = self._installed_routes.get(drone_id)
        if mode in {"restricted", "control_only"} and control_path not in {
            "direct", "satellite", "mesh"
        }:
            return {"status": "error", "reason": "control_route_unavailable"}
        datapaths = [
            _KNOWN_DATAPATHS[dpid]
            for dpid in sorted(self._topology.expected_dpids)
        ]
        try:
            for datapath in datapaths:
                install_containment(
                    datapath, drone_id, mode, control_path=control_path
                )
        except Exception as exc:
            logger.exception("Containment installation failed for %s", drone_id)
            return {"status": "error", "reason": "containment_installation_failed", "error": str(exc)}
        acknowledged, timed_out = self._wait_for_barriers(datapaths)
        result = {
            "status": "uncertain" if timed_out else "ok",
            "drone_id": drone_id,
            "mode": mode,
            "reason": reason,
            "enforcement": (
                "allow_all" if mode == "normal" else
                "control_only" if mode in {"restricted", "control_only"} else
                "drop_all"
            ),
            "acknowledged_switches": acknowledged,
            "unacknowledged_switches": timed_out,
        }
        self._containment[drone_id] = result
        return result


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
        if path_name not in ROUTE_COMMANDS or not is_active_drone(drone_id):
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

    @route("sdn", "/sdn/containment", methods=["POST"])
    def containment(self, req, **kwargs):
        if not self._authorized(req):
            return self._unauthorized_response()
        try:
            body = json.loads(req.body)
        except json.JSONDecodeError:
            return Response(status=400, json={"error": "Invalid JSON"})
        drone_id = body.get("drone_id")
        mode = body.get("mode")
        reason = str(body.get("reason", "cross_layer_policy"))
        if not is_active_drone(drone_id) or mode not in {
            "normal", "restricted", "control_only", "quarantined"
        }:
            return Response(
                status=422,
                content_type="application/json",
                body=json.dumps({"error": "Invalid drone_id or containment mode"}).encode(),
            )
        result = self.controller.apply_containment(drone_id, mode, reason)
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
            body=json.dumps({
                "switches": switches,
                "installed_routes": self.controller._installed_routes,
                "containment": self.controller._containment,
                "topology": self.controller._topology.snapshot(),
            }).encode(),
        )

    @route("sdn", "/sdn/evidence/{drone_id}", methods=["GET"])
    def evidence(self, req, drone_id, **kwargs):
        if not self._authorized(req):
            return self._unauthorized_response()
        if not is_active_drone(drone_id):
            return Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "Unknown drone_id"}).encode(),
            )
        snapshot = self.controller._evidence.snapshot(drone_id)
        return Response(
            status=200 if snapshot.get("available") else 503,
            content_type="application/json",
            body=json.dumps(snapshot).encode(),
        )

    @route("sdn", "/health", methods=["GET"])
    def health(self, req, **kwargs):
        snapshot = self.controller._topology.snapshot()
        return Response(
            content_type="application/json",
            body=json.dumps({
                "status": "ok",
                "mode": "real",
                "ready": snapshot["ready"],
                "switches": len(_KNOWN_DATAPATHS),
                "topology": snapshot,
            }).encode(),
        )

    @route("sdn", "/ready", methods=["GET"])
    def readiness(self, req, **kwargs):
        snapshot = self.controller._topology.snapshot()
        return Response(
            status=200 if snapshot["ready"] else 503,
            content_type="application/json",
            body=json.dumps({
                "status": "ready" if snapshot["ready"] else "not_ready",
                "topology": snapshot,
            }).encode(),
        )
