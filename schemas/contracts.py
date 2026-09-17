"""Versioned cross-layer contracts shared by FLARE components.

Old artifacts remain readable; new security behavior is introduced under new
contract identifiers so a checkpoint can never silently receive a different
observation or action meaning.
"""

from __future__ import annotations

from dataclasses import dataclass


THREAT_MODEL_CONTRACT = "threat_model_v2"
THREAT_FEATURES = ("rssi", "pdr", "sinr", "latency", "packet_loss")

ROUTING_STATE_V2 = "routing_state_v2"
ROUTING_STATE_V3 = "routing_state_v3"
ROUTING_ACTIONS_V2 = ("direct", "satellite", "mesh")
ROUTING_ACTIONS_V3 = (*ROUTING_ACTIONS_V2, "hold")
ROUTING_OBSERVATION_DIMENSIONS = {
    ROUTING_STATE_V2: 14,
    ROUTING_STATE_V3: 18,
}
ROUTING_ACTION_COUNTS = {
    ROUTING_STATE_V2: len(ROUTING_ACTIONS_V2),
    ROUTING_STATE_V3: len(ROUTING_ACTIONS_V3),
}

DECISION_EVENT_V2 = "2.1"
DECISION_EVENT_V3 = "3.0"
INSIDER_EVIDENCE_CONTRACT = "insider_evidence_v2"
NETWORK_SECURITY_EVIDENCE_CONTRACT = "network_security_evidence_v2"
EXPLANATION_CONTRACT = "integrated_gradients_v1"
ROUTING_EXPLANATION_CONTRACT = "routing_integrated_gradients_v1"


@dataclass(frozen=True)
class RoutingContract:
    name: str
    observation_dim: int
    actions: tuple[str, ...]


def routing_contract(name: str) -> RoutingContract:
    """Return a validated routing contract description."""
    actions: tuple[str, ...]
    if name == ROUTING_STATE_V2:
        actions = ROUTING_ACTIONS_V2
    elif name == ROUTING_STATE_V3:
        actions = ROUTING_ACTIONS_V3
    else:
        raise ValueError(f"unsupported routing contract: {name!r}")
    return RoutingContract(
        name=name,
        observation_dim=ROUTING_OBSERVATION_DIMENSIONS[name],
        actions=actions,
    )
