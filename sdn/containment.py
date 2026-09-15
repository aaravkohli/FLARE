"""Cross-layer trust/risk policy for executable SDN containment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import time


class ContainmentMode(str, Enum):
    NORMAL = "normal"
    RESTRICTED = "restricted"
    CONTROL_ONLY = "control_only"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class ContainmentDecision:
    drone_id: str
    mode: ContainmentMode
    reason: str
    trust_score: float
    insider_risk: float
    network_risk: float | None
    drivers: tuple[str, ...]
    decided_at: float

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["drivers"] = list(self.drivers)
        return payload


def decide_containment(
    drone_id: str,
    *,
    trust_score: float,
    insider_risk: float,
    network_risk: float | None = None,
    network_response_hint: str | None = None,
    restricted_threshold: float = 0.35,
    control_only_threshold: float = 0.72,
    quarantine_threshold: float = 0.90,
) -> ContainmentDecision:
    """Fuse FL reputation and independently observed insider evidence."""
    trust = min(1.0, max(0.0, float(trust_score)))
    risk = min(1.0, max(0.0, float(insider_risk)))
    network = min(1.0, max(0.0, float(network_risk or 0.0)))
    combined = max(risk, network, 1.0 - trust)
    drivers = []
    if 1.0 - trust >= restricted_threshold:
        drivers.append("fl_trust")
    if risk >= restricted_threshold:
        drivers.append("insider_evidence")
    if network >= restricted_threshold:
        drivers.append("network_evidence")
    if network_response_hint == "quarantined":
        mode, reason = ContainmentMode.QUARANTINED, "network_identity_mismatch"
    elif combined >= quarantine_threshold:
        mode, reason = ContainmentMode.QUARANTINED, "critical_cross_layer_risk"
    elif combined >= control_only_threshold:
        mode, reason = ContainmentMode.CONTROL_ONLY, "malicious_evidence"
    elif combined >= restricted_threshold:
        mode, reason = ContainmentMode.RESTRICTED, "suspicious_evidence"
    else:
        mode, reason = ContainmentMode.NORMAL, "risk_below_threshold"
    return ContainmentDecision(
        drone_id=drone_id,
        mode=mode,
        reason=reason,
        trust_score=trust,
        insider_risk=risk,
        network_risk=network_risk,
        drivers=tuple(drivers),
        decided_at=time.time(),
    )
