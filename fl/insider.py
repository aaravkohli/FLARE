"""Evidence-based telemetry insider detection for UAV participants.

This detector intentionally uses controller-observed counters in addition to
drone-reported values.  It is separate from the RF BiLSTM: the BiLSTM detects
communication threats, while this temporal evidence model detects selective
forwarding, telemetry falsification, control flooding, and replay behavior.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import math
from typing import Deque, Mapping, Optional

import numpy as np

from schemas.contracts import INSIDER_EVIDENCE_CONTRACT


INSIDER_CLASSES = (
    "normal",
    "selective_forwarding",
    "telemetry_falsification",
    "control_flood",
    "replay",
)


@dataclass(frozen=True)
class InsiderEvidence:
    reported_tx_packets: int
    controller_rx_packets: int
    controller_forwarded_packets: int
    reported_forwarded_packets: int
    control_messages_per_s: float
    duplicate_sequence_ratio: Optional[float]
    timestamp: float
    controller_timestamp: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "InsiderEvidence":
        evidence = cls(
            reported_tx_packets=int(value["reported_tx_packets"]),
            controller_rx_packets=int(value["controller_rx_packets"]),
            controller_forwarded_packets=int(value["controller_forwarded_packets"]),
            reported_forwarded_packets=int(value["reported_forwarded_packets"]),
            control_messages_per_s=float(value["control_messages_per_s"]),
            duplicate_sequence_ratio=(
                float(value["duplicate_sequence_ratio"])
                if value.get("duplicate_sequence_ratio") is not None else None
            ),
            timestamp=float(value["timestamp"]),
            controller_timestamp=float(value["controller_timestamp"]),
        )
        numeric = asdict(evidence)
        if any(
            not math.isfinite(float(item))
            for item in numeric.values() if item is not None
        ):
            raise ValueError("insider evidence contains a non-finite value")
        if any(numeric[key] < 0 for key in (
            "reported_tx_packets",
            "controller_rx_packets",
            "controller_forwarded_packets",
            "reported_forwarded_packets",
            "control_messages_per_s",
        )):
            raise ValueError("insider evidence values cannot be negative")
        if (
            evidence.duplicate_sequence_ratio is not None
            and evidence.duplicate_sequence_ratio > 1.0
        ):
            raise ValueError("duplicate_sequence_ratio must be in [0, 1]")
        return evidence


@dataclass
class InsiderAnalysis:
    client_id: str
    status: str
    predicted_class: str
    risk_score: float
    instantaneous_risk: float
    forwarding_ratio: float
    telemetry_claim_gap: float
    control_rate_score: float
    replay_score: float
    evidence_freshness: float
    evidence_contract: str = INSIDER_EVIDENCE_CONTRACT

    def to_dict(self) -> dict:
        payload = asdict(self)
        return {
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in payload.items()
        }


class InsiderTelemetryAnalyzer:
    """Stateful multi-signal insider detector with hysteresis.

    Thresholds are operational policy and externally configurable.  Risk is an
    EWMA, so one transient counter mismatch is suspicious at most; sustained or
    strongly corroborated evidence is required for a malicious decision.
    """

    def __init__(
        self,
        *,
        min_packets: int = 20,
        forwarding_ratio_floor: float = 0.75,
        claim_gap_threshold: float = 0.25,
        control_rate_threshold: float = 25.0,
        replay_ratio_threshold: float = 0.20,
        suspicious_threshold: float = 0.35,
        malicious_threshold: float = 0.72,
        classification_signal_threshold: float = 0.50,
        history_alpha: float = 0.65,
        max_evidence_age_s: float = 3.0,
        history_size: int = 20,
    ):
        self.min_packets = max(1, int(min_packets))
        self.forwarding_ratio_floor = float(forwarding_ratio_floor)
        self.claim_gap_threshold = float(claim_gap_threshold)
        self.control_rate_threshold = float(control_rate_threshold)
        self.replay_ratio_threshold = float(replay_ratio_threshold)
        self.suspicious_threshold = float(suspicious_threshold)
        self.malicious_threshold = float(malicious_threshold)
        self.classification_signal_threshold = float(classification_signal_threshold)
        self.history_alpha = float(history_alpha)
        self.max_evidence_age_s = max(0.001, float(max_evidence_age_s))
        self._risk: dict[str, float] = {}
        self._history: dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=max(3, int(history_size)))
        )

    @staticmethod
    def _severity(value: float, threshold: float, *, inverse: bool = False) -> float:
        if inverse:
            return float(np.clip((threshold - value) / max(threshold, 1e-9), 0.0, 1.0))
        return float(np.clip(value / max(threshold, 1e-9), 0.0, 1.0))

    def analyze(self, client_id: str, raw: Mapping[str, object]) -> InsiderAnalysis:
        evidence = InsiderEvidence.from_mapping(raw)
        received = max(evidence.controller_rx_packets, 1)
        forwarding_ratio = min(
            1.0, evidence.controller_forwarded_packets / received
        )
        claim_gap = abs(
            evidence.reported_forwarded_packets - evidence.controller_forwarded_packets
        ) / max(evidence.reported_forwarded_packets, evidence.controller_forwarded_packets, 1)
        enough_packets = evidence.controller_rx_packets >= self.min_packets
        drop_score = (
            self._severity(
                forwarding_ratio, self.forwarding_ratio_floor, inverse=True
            )
            if enough_packets
            else 0.0
        )
        claim_score = self._severity(claim_gap, self.claim_gap_threshold)
        control_score = self._severity(
            evidence.control_messages_per_s, self.control_rate_threshold
        )
        replay_score = (
            self._severity(
                evidence.duplicate_sequence_ratio, self.replay_ratio_threshold
            )
            if evidence.duplicate_sequence_ratio is not None else 0.0
        )
        age = abs(evidence.controller_timestamp - evidence.timestamp)
        freshness = float(np.clip(1.0 - age / self.max_evidence_age_s, 0.0, 1.0))

        components = {
            "selective_forwarding": 0.40 * drop_score,
            "telemetry_falsification": 0.30 * claim_score,
            "control_flood": 0.18 * control_score,
            "replay": 0.12 * replay_score,
        }
        raw_signals = {
            "selective_forwarding": drop_score,
            "telemetry_falsification": claim_score,
            "control_flood": control_score,
            "replay": replay_score,
        }
        instantaneous = min(1.0, sum(components.values()) * (0.5 + 0.5 * freshness))
        previous = self._risk.get(client_id, 0.0)
        risk = self.history_alpha * previous + (1.0 - self.history_alpha) * instantaneous
        # Strong two-source contradictions must be actionable in their first
        # observation; hysteresis is for recovery and marginal evidence.
        corroborated = sum(score >= 0.8 for score in (drop_score, claim_score, control_score, replay_score)) >= 2
        if corroborated:
            risk = max(risk, self.malicious_threshold)
        self._risk[client_id] = float(np.clip(risk, 0.0, 1.0))
        self._history[client_id].append(instantaneous)

        predicted_class = max(components, key=components.get)
        if max(raw_signals.values(), default=0.0) < self.classification_signal_threshold:
            predicted_class = "normal"
        status = (
            "MALICIOUS"
            if risk >= self.malicious_threshold
            else "SUSPICIOUS"
            if risk >= self.suspicious_threshold
            else "NORMAL"
        )
        return InsiderAnalysis(
            client_id=client_id,
            status=status,
            predicted_class=predicted_class,
            risk_score=float(np.clip(risk, 0.0, 1.0)),
            instantaneous_risk=instantaneous,
            forwarding_ratio=forwarding_ratio,
            telemetry_claim_gap=claim_gap,
            control_rate_score=control_score,
            replay_score=replay_score,
            evidence_freshness=freshness,
        )

    def risk(self, client_id: str) -> float:
        return self._risk.get(client_id, 0.0)
