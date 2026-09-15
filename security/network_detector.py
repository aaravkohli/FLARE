"""Explicit DoS and network/telemetry-spoofing detector.

Scenario names and jammer state are deliberately absent from detector inputs.
They may be used by experiment code as ground truth, never as features.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any, Mapping

import numpy as np


NETWORK_EVIDENCE_CONTRACT = "network_security_evidence_v1"


@dataclass(frozen=True)
class NetworkThreatAnalysis:
    status: str
    detected_classes: tuple[str, ...]
    risk_score: float | None
    dos_score: float
    spoofing_score: float
    drop_score: float
    rate_score: float
    latency_score: float
    identity_mismatch: bool
    claim_gap_score: float
    duplicate_score: float
    evidence_freshness: float
    evidence_source: str
    evidence_independent: bool
    response_hint: str
    reason: str
    available_signals: tuple[str, ...]
    evidence_contract: str = NETWORK_EVIDENCE_CONTRACT

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["detected_classes"] = list(self.detected_classes)
        payload["available_signals"] = list(self.available_signals)
        return {
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in payload.items()
        }


class NetworkThreatAnalyzer:
    """Stateful deterministic detector over source-labelled evidence."""

    def __init__(self, config: Mapping[str, Any]):
        self.config = dict(config)
        self.suspicious_threshold = float(config.get("suspicious_threshold", 0.35))
        self.malicious_threshold = float(config.get("malicious_threshold", 0.72))
        self.critical_threshold = float(config.get("critical_threshold", 0.90))
        self.history_alpha = float(config.get("history_alpha", 0.65))
        self.dos = dict(config.get("dos", {}))
        self.spoofing = dict(config.get("spoofing", {}))
        self._risk: dict[str, float] = {}

    @staticmethod
    def _ratio_score(value: float, threshold: float) -> float:
        return float(np.clip(value / max(threshold, 1e-9), 0.0, 1.0))

    def unavailable(self, reason: str, source: str = "unavailable") -> NetworkThreatAnalysis:
        return NetworkThreatAnalysis(
            status="UNAVAILABLE",
            detected_classes=(),
            risk_score=None,
            dos_score=0.0,
            spoofing_score=0.0,
            drop_score=0.0,
            rate_score=0.0,
            latency_score=0.0,
            identity_mismatch=False,
            claim_gap_score=0.0,
            duplicate_score=0.0,
            evidence_freshness=0.0,
            evidence_source=source,
            evidence_independent=False,
            response_hint="none",
            reason=reason,
            available_signals=(),
        )

    def analyze(
        self,
        client_id: str,
        raw: Mapping[str, Any] | None,
        *,
        max_path_latency_ms: float,
        max_evidence_age_s: float = 3.0,
        require_independent: bool = False,
        now: float | None = None,
    ) -> NetworkThreatAnalysis:
        if not isinstance(raw, Mapping):
            return self.unavailable("evidence_missing")
        provenance = raw.get("provenance")
        if not isinstance(provenance, Mapping):
            return self.unavailable("evidence_provenance_missing", "legacy_unknown")
        source = str(provenance.get("category", "unknown"))
        independent = bool(provenance.get("independent", False))
        if require_independent and not independent:
            return self.unavailable("independent_controller_evidence_required", source)
        try:
            age_limit = float(max_evidence_age_s)
            observed_now = float(now if now is not None else time.time())
        except (TypeError, ValueError):
            return self.unavailable("evidence_policy_invalid", source)
        if not math.isfinite(age_limit) or age_limit <= 0 or not math.isfinite(observed_now):
            return self.unavailable("evidence_policy_invalid", source)
        try:
            controller_timestamp = float(raw["controller_timestamp"])
            age = max(0.0, observed_now - controller_timestamp)
            numeric = [
                float(raw[key])
                for key in (
                    "reported_tx_packets",
                    "reported_forwarded_packets",
                    "controller_rx_packets",
                    "controller_forwarded_packets",
                    "control_messages_per_s",
                    "timestamp",
                )
            ]
            for optional_key in (
                "controller_dropped_packets", "packet_rate_per_s",
                "duplicate_sequence_ratio", "controller_policy_dropped_packets",
                "controller_port_rx_packets", "controller_port_dropped_packets",
                "controller_port_error_packets", "port_timestamp",
            ):
                optional_value = raw.get(optional_key)
                if optional_value is not None:
                    numeric.append(float(optional_value))
        except (KeyError, TypeError, ValueError):
            return self.unavailable("evidence_contract_invalid", source)
        if any(not math.isfinite(value) or value < 0.0 for value in numeric):
            return self.unavailable("evidence_contract_invalid", source)
        if not math.isfinite(controller_timestamp) or controller_timestamp < 0.0:
            return self.unavailable("evidence_contract_invalid", source)
        for counter_name in (
            "reported_tx_packets", "reported_forwarded_packets",
            "controller_rx_packets", "controller_forwarded_packets",
        ):
            if not float(raw[counter_name]).is_integer():
                return self.unavailable("evidence_contract_invalid", source)
        if float(raw["controller_forwarded_packets"]) > float(raw["controller_rx_packets"]):
            return self.unavailable("evidence_contract_invalid", source)
        if raw.get("duplicate_sequence_ratio") is not None and float(
            raw["duplicate_sequence_ratio"]
        ) > 1.0:
            return self.unavailable("evidence_contract_invalid", source)
        try:
            reported_timestamp = float(raw["timestamp"])
            path_latency = float(max_path_latency_ms)
        except (TypeError, ValueError):
            return self.unavailable("evidence_contract_invalid", source)
        if not math.isfinite(path_latency) or path_latency < 0.0:
            return self.unavailable("evidence_contract_invalid", source)
        if controller_timestamp > observed_now + 0.5:
            return self.unavailable("evidence_timestamp_future", source)
        if age > age_limit:
            return self.unavailable("evidence_stale", source)
        if abs(reported_timestamp - controller_timestamp) > age_limit:
            return self.unavailable("evidence_timestamp_misaligned", source)
        freshness = float(np.clip(1.0 - age / age_limit, 0.0, 1.0))

        port_keys = (
            "controller_port_rx_packets", "controller_port_dropped_packets",
            "controller_port_error_packets", "port_timestamp",
            "controller_dropped_packets", "drop_counter_semantics",
        )
        port_present = any(raw.get(key) is not None for key in port_keys)
        port_drop_available = False
        if source == "ryu_openflow":
            if port_present and any(raw.get(key) is None for key in port_keys):
                return self.unavailable("port_drop_contract_invalid", source)
            if port_present:
                if raw["drop_counter_semantics"] != "ingress_port_receive_drop_error":
                    return self.unavailable("port_drop_semantics_invalid", source)
                port_time = float(raw["port_timestamp"])
                if (
                    port_time > observed_now + 0.5
                    or observed_now - port_time > age_limit
                    or abs(port_time - controller_timestamp) > age_limit
                ):
                    return self.unavailable("port_drop_timestamp_invalid", source)
                if any(not float(raw[key]).is_integer() for key in port_keys[:3]):
                    return self.unavailable("port_drop_contract_invalid", source)
                dropped = float(raw["controller_port_dropped_packets"]) + float(
                    raw["controller_port_error_packets"]
                )
                if dropped != float(raw["controller_dropped_packets"]):
                    return self.unavailable("port_drop_contract_invalid", source)
                received = float(raw["controller_port_rx_packets"])
                port_drop_available = True
            else:
                received, dropped = 0.0, 0.0
        else:
            received = max(float(raw["controller_rx_packets"]), 1.0)
            dropped = max(
                float(raw.get("controller_dropped_packets") or 0.0),
                received - float(raw["controller_forwarded_packets"]),
            )
            port_drop_available = True
        drop_ratio = dropped / max(received + dropped, 1.0)
        drop_score = self._ratio_score(
            drop_ratio, float(self.dos.get("drop_ratio_threshold", 0.50))
        )
        rate = max(
            float(raw.get("packet_rate_per_s") or 0.0),
            float(raw["control_messages_per_s"]),
        )
        rate_score = self._ratio_score(
            rate, float(self.dos.get("packet_rate_threshold", 250.0))
        )
        latency_score = self._ratio_score(
            path_latency,
            float(self.dos.get("latency_threshold_ms", 750.0)),
        )
        dos_weights = self.dos.get("weights", {})
        dos_score = float(np.clip(
            float(dos_weights.get("drop", 0.45)) * drop_score
            + float(dos_weights.get("rate", 0.35)) * rate_score
            + float(dos_weights.get("latency", 0.20)) * latency_score,
            0.0,
            1.0,
        ))

        observed_mac = str(raw.get("observed_source_mac") or "").lower()
        expected_mac = str(raw.get("expected_source_mac") or "").lower()
        identity_mismatch = bool(observed_mac and expected_mac and observed_mac != expected_mac)
        identity_observed = raw.get("identity_observed_at")
        if require_independent and identity_mismatch:
            try:
                identity_timestamp = float(identity_observed)
            except (TypeError, ValueError):
                return self.unavailable("identity_observation_untrusted", source)
            if (
                not math.isfinite(identity_timestamp)
                or identity_timestamp > observed_now + 0.5
                or observed_now - identity_timestamp > age_limit
            ):
                return self.unavailable("identity_observation_untrusted", source)
        reported_forwarded = float(raw["reported_forwarded_packets"])
        controller_forwarded = float(raw["controller_forwarded_packets"])
        claim_gap = abs(reported_forwarded - controller_forwarded) / max(
            reported_forwarded, controller_forwarded, 1.0
        )
        claim_score = self._ratio_score(
            claim_gap,
            float(self.spoofing.get("claim_gap_threshold", 0.25)),
        )
        duplicate_available = raw.get("duplicate_sequence_ratio") is not None
        duplicate_score = (
            self._ratio_score(
                float(raw["duplicate_sequence_ratio"]),
                float(self.spoofing.get("duplicate_ratio_threshold", 0.20)),
            )
            if duplicate_available else 0.0
        )
        spoof_weights = self.spoofing.get("weights", {})
        spoofing_score = float(np.clip(
            float(spoof_weights.get("identity", 0.60)) * float(identity_mismatch)
            + float(spoof_weights.get("claim_gap", 0.25)) * claim_score
            + float(spoof_weights.get("duplicate", 0.15)) * duplicate_score,
            0.0,
            1.0,
        ))

        dos_corroborated = sum(score >= 0.8 for score in (drop_score, rate_score, latency_score)) >= 2
        spoof_corroborated = identity_mismatch or sum(
            score >= 0.8 for score in (claim_score, duplicate_score)
        ) >= 2
        instantaneous = max(dos_score, spoofing_score) * (0.5 + 0.5 * freshness)
        previous = self._risk.get(client_id, 0.0)
        risk = self.history_alpha * previous + (1.0 - self.history_alpha) * instantaneous
        if identity_mismatch:
            risk = max(risk, self.critical_threshold)
        elif dos_corroborated or spoof_corroborated:
            risk = max(risk, self.malicious_threshold)
        risk = float(np.clip(risk, 0.0, 1.0))
        self._risk[client_id] = risk
        status = (
            "MALICIOUS" if risk >= self.malicious_threshold
            else "SUSPICIOUS" if risk >= self.suspicious_threshold
            else "NORMAL"
        )
        detected = []
        if dos_score >= self.suspicious_threshold:
            detected.append("dos")
        if spoofing_score >= self.suspicious_threshold:
            detected.append("network_spoofing")
        response = "normal"
        if status == "SUSPICIOUS":
            response = "restricted"
        elif status == "MALICIOUS":
            response = "quarantined" if identity_mismatch else "control_only"
        available_signals = ["control_rate", "latency", "claim_gap"]
        if port_drop_available:
            available_signals.append("port_receive_drop" if source == "ryu_openflow" else "drop")
        if raw.get("controller_policy_dropped_packets") is not None:
            available_signals.append("policy_drop_not_scored")
        if raw.get("packet_rate_per_s") is not None:
            available_signals.append("packet_rate")
        if observed_mac and expected_mac and (
            not require_independent or identity_observed is not None
        ):
            available_signals.append("source_identity")
        if duplicate_available:
            available_signals.append("duplicate_replay")
        return NetworkThreatAnalysis(
            status=status,
            detected_classes=tuple(detected),
            risk_score=risk,
            dos_score=dos_score,
            spoofing_score=spoofing_score,
            drop_score=drop_score,
            rate_score=rate_score,
            latency_score=latency_score,
            identity_mismatch=identity_mismatch,
            claim_gap_score=claim_score,
            duplicate_score=duplicate_score,
            evidence_freshness=freshness,
            evidence_source=source,
            evidence_independent=independent,
            response_hint=response,
            reason=("identity_mismatch" if identity_mismatch else "evidence_scored"),
            available_signals=tuple(available_signals),
        )
