"""Versioned canonical event emitted for every drone routing decision."""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PathMetrics(_StrictModel):
    path_id: Literal["direct", "satellite", "mesh"]
    rssi: float = Field(ge=-130.0, le=0.0)
    pdr: float = Field(ge=0.0, le=1.0)
    sinr: float
    latency: float = Field(ge=0.0)
    packet_loss: float = Field(ge=0.0, le=1.0)


class GpsMetrics(_StrictModel):
    latitude: float
    longitude: float
    drift_m: float = Field(default=0.0, ge=0.0)


class EwStatus(_StrictModel):
    active_attack: Optional[str] = None
    jammed_paths: list[Literal["direct", "satellite", "mesh"]] = Field(
        default_factory=list
    )


class SecurityEvidence(_StrictModel):
    reported_tx_packets: int = Field(ge=0)
    controller_rx_packets: int = Field(ge=0)
    controller_forwarded_packets: int = Field(ge=0)
    reported_forwarded_packets: int = Field(ge=0)
    control_messages_per_s: float = Field(ge=0.0)
    duplicate_sequence_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    timestamp: float = Field(gt=0.0)
    controller_timestamp: float = Field(gt=0.0)
    counter_scope: Optional[Literal["aligned_sample_window_v1"]] = None
    report_window_seconds: Optional[float] = Field(default=None, gt=0.0, le=3.0)
    flow_window_seconds: Optional[float] = Field(default=None, gt=0.0, le=3.0)
    report_window_start: Optional[float] = Field(default=None, gt=0.0)
    flow_window_start: Optional[float] = Field(default=None, gt=0.0)
    window_alignment_s: Optional[float] = Field(default=None, ge=0.0, le=3.0)
    simulation_profile: Optional[str] = None
    controller_dropped_packets: Optional[int] = Field(default=None, ge=0)
    controller_policy_dropped_packets: Optional[int] = Field(default=None, ge=0)
    controller_policy_total_dropped_packets: Optional[int] = Field(default=None, ge=0)
    controller_flow_total_rx_packets: Optional[int] = Field(default=None, ge=0)
    controller_flow_total_forwarded_packets: Optional[int] = Field(default=None, ge=0)
    controller_port_rx_packets: Optional[int] = Field(default=None, ge=0)
    controller_port_dropped_packets: Optional[int] = Field(default=None, ge=0)
    controller_port_error_packets: Optional[int] = Field(default=None, ge=0)
    controller_port_window_rx_packets: Optional[int] = Field(default=None, ge=0)
    controller_port_window_dropped_packets: Optional[int] = Field(default=None, ge=0)
    controller_port_window_error_packets: Optional[int] = Field(default=None, ge=0)
    port_window_seconds: Optional[float] = Field(default=None, gt=0.0)
    port_timestamp: Optional[float] = Field(default=None, gt=0.0)
    drop_counter_semantics: Optional[Literal["ingress_port_receive_drop_error"]] = None
    packet_rate_per_s: Optional[float] = Field(default=None, ge=0.0)
    observed_source_mac: Optional[str] = None
    expected_source_mac: Optional[str] = None
    identity_observed_at: Optional[float] = Field(default=None, gt=0.0)
    control_rate_observer: Optional[str] = None
    provenance: Optional["EvidenceProvenance"] = None


class EvidenceProvenance(_StrictModel):
    category: Literal[
        "synthetic_simulation", "mock_sdn", "ryu_openflow", "real_sensor", "unavailable"
    ]
    observer_id: str = Field(min_length=1)
    independent: bool
    collected_at: float = Field(gt=0.0)
    age_s: float = Field(ge=0.0)


class TelemetrySnapshot(_StrictModel):
    drone_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]{2,63}$",
    )
    timestamp: float = Field(gt=0.0)
    source: Literal[
        "synthetic", "live", "cached_live", "synthetic_fallback", "legacy_api_fallback"
    ]
    source_collected_at: Optional[float] = Field(default=None, gt=0.0)
    source_age_s: Optional[float] = Field(default=None, ge=0.0)
    paths: list[PathMetrics] = Field(min_length=3, max_length=3)
    gps: Optional[GpsMetrics] = None
    ew_status: Optional[EwStatus] = None
    security_evidence: Optional[SecurityEvidence] = None

    @field_validator("paths")
    @classmethod
    def validate_complete_path_set(cls, paths: list[PathMetrics]) -> list[PathMetrics]:
        path_ids = [path.path_id for path in paths]
        expected = {"direct", "satellite", "mesh"}
        if len(set(path_ids)) != 3 or set(path_ids) != expected:
            raise ValueError("paths must contain direct, satellite, and mesh exactly once")
        return paths


class InferenceTrace(_StrictModel):
    path_scores: list[float] = Field(min_length=3, max_length=3)
    confidence: float = Field(ge=0.0, le=1.0)
    attack_type: str
    source: Literal["fl_model", "heuristic_fallback"]
    model_id: Optional[str] = None
    model_generation: Optional[int] = Field(default=None, ge=0)
    normalization_version: str = "rf-v1"

    @field_validator("path_scores")
    @classmethod
    def validate_path_scores(cls, scores: list[float]) -> list[float]:
        if any(score < 0.0 or score > 1.0 for score in scores):
            raise ValueError("path scores must be in [0, 1]")
        return scores


class DecisionTrace(_StrictModel):
    source: Literal["rl_model", "greedy_fallback"]
    model_id: Optional[str] = None
    model_generation: Optional[int] = Field(default=None, ge=0)
    policy_action_id: int = Field(ge=0, le=3)
    policy_path: Literal["direct", "satellite", "mesh", "hold"]
    requested_action_id: int = Field(ge=0, le=3)
    requested_path: Literal["direct", "satellite", "mesh", "hold"]
    installed_action_id: Optional[int] = Field(default=None, ge=0, le=3)
    installed_path: Optional[Literal["direct", "satellite", "mesh", "hold"]] = None
    network_action: Literal["forward", "hold"] = "forward"
    threat_level: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    observation: Optional[list[float]] = None
    safety_override: bool = False
    safe_action_mask: list[bool] = Field(
        default_factory=lambda: [True, True, True],
        min_length=3,
        max_length=3,
    )
    no_safe_route: bool = False
    constraint_reason: Optional[
        Literal[
            "policy_route_above_threshold",
            "all_routes_above_threshold",
            "containment_required",
        ]
    ] = None
    safety_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    all_unsafe_behavior: Literal["least_risk_route", "hold"] = "least_risk_route"
    routing_contract: Literal["routing_state_v2", "routing_state_v3"] = "routing_state_v2"
    route_changed: bool = False

    @model_validator(mode="after")
    def validate_constraint_state(self):
        if self.observation is not None:
            expected = 18 if self.routing_contract == "routing_state_v3" else 14
            if len(self.observation) != expected:
                raise ValueError(
                    f"{self.routing_contract} requires an observation of length {expected}"
                )
        if self.network_action == "hold" and (
            self.requested_path != "hold" or self.requested_action_id != 3
        ):
            raise ValueError("network_action=hold requires requested hold action 3")
        if self.no_safe_route and any(self.safe_action_mask):
            raise ValueError("no_safe_route requires every safe_action_mask value to be false")
        if self.no_safe_route and self.constraint_reason != "all_routes_above_threshold":
            raise ValueError(
                "no_safe_route requires constraint_reason=all_routes_above_threshold"
            )
        if self.constraint_reason == "all_routes_above_threshold" and not self.no_safe_route:
            raise ValueError(
                "all_routes_above_threshold requires no_safe_route=true"
            )
        return self


class SdnTrace(_StrictModel):
    applied: bool
    response: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class RewardComponents(_StrictModel):
    throughput_norm: float = Field(ge=0.0, le=1.0)
    delay_norm: float = Field(ge=0.0, le=1.0)
    energy_norm: float = Field(ge=0.0, le=1.0)
    loss_norm: float = Field(ge=0.0, le=1.0)
    switched: bool
    throughput_contribution: float = Field(ge=0.0)
    delay_penalty: float = Field(ge=0.0)
    energy_penalty: float = Field(ge=0.0)
    loss_penalty: float = Field(ge=0.0)
    switching_penalty: float = Field(ge=0.0)
    total: float
    held: bool = False


class OutcomeTrace(_StrictModel):
    reward: float
    reward_definition: Literal["routing_qos_v2", "routing_security_qos_v3"]
    reward_components: RewardComponents
    path: Literal["direct", "satellite", "mesh", "hold"]
    estimated: bool = False
    recovery_ms: Optional[float] = Field(default=None, ge=0.0)
    packet_loss: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_reward_total(self):
        if not math.isclose(
            self.reward,
            self.reward_components.total,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("reward must equal reward_components.total")
        return self


class TimingTrace(_StrictModel):
    tick_elapsed_ms: Optional[float] = Field(default=None, ge=0.0)


class InsiderTrace(_StrictModel):
    client_id: str = Field(min_length=1)
    status: Literal["NORMAL", "SUSPICIOUS", "MALICIOUS"]
    predicted_class: Literal[
        "normal",
        "selective_forwarding",
        "telemetry_falsification",
        "control_flood",
        "replay",
    ]
    risk_score: float = Field(ge=0.0, le=1.0)
    instantaneous_risk: float = Field(ge=0.0, le=1.0)
    forwarding_ratio: float = Field(ge=0.0, le=1.0)
    telemetry_claim_gap: float = Field(ge=0.0, le=1.0)
    control_rate_score: float = Field(ge=0.0, le=1.0)
    replay_score: float = Field(ge=0.0, le=1.0)
    evidence_freshness: float = Field(ge=0.0, le=1.0)
    policy_filtered: bool = False
    evidence_contract: Literal[
        "insider_evidence_v1", "insider_evidence_v2"
    ] = "insider_evidence_v1"


class ContainmentTrace(_StrictModel):
    """Cross-layer response selected from insider risk and FL trust."""

    drone_id: str = Field(min_length=1)
    mode: Literal["normal", "restricted", "control_only", "quarantined"]
    reason: str
    trust_score: float = Field(ge=0.0, le=1.0)
    insider_risk: float = Field(ge=0.0, le=1.0)
    network_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    drivers: list[str] = Field(default_factory=list)
    decided_at: float = Field(gt=0.0)


class NetworkSecurityTrace(_StrictModel):
    status: Literal["NORMAL", "SUSPICIOUS", "MALICIOUS", "UNAVAILABLE"]
    detected_classes: list[Literal["dos", "network_spoofing"]] = Field(
        default_factory=list
    )
    risk_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    dos_score: float = Field(ge=0.0, le=1.0)
    spoofing_score: float = Field(ge=0.0, le=1.0)
    drop_score: float = Field(ge=0.0, le=1.0)
    rate_score: float = Field(ge=0.0, le=1.0)
    latency_score: float = Field(ge=0.0, le=1.0)
    identity_mismatch: bool
    claim_gap_score: float = Field(ge=0.0, le=1.0)
    duplicate_score: float = Field(ge=0.0, le=1.0)
    evidence_freshness: float = Field(ge=0.0, le=1.0)
    evidence_source: str
    evidence_independent: bool
    response_hint: Literal["none", "normal", "restricted", "control_only", "quarantined"]
    reason: str
    available_signals: list[str] = Field(default_factory=list)
    evidence_contract: Literal[
        "network_security_evidence_v1", "network_security_evidence_v2"
    ]


class DecisionEvent(_StrictModel):
    """The authoritative record for one telemetry → decision → SDN cycle."""

    schema_version: Literal["2.0", "2.1", "3.0", "3.1"] = "2.1"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step: int = Field(ge=1)
    timestamp: float = Field(gt=0.0)
    drone_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]{2,63}$",
    )
    mode: Literal["simulation", "real"]
    telemetry: TelemetrySnapshot
    inference: InferenceTrace
    insider_analysis: Optional[InsiderTrace] = None
    network_security_analysis: Optional[NetworkSecurityTrace] = None
    containment: Optional[ContainmentTrace] = None
    decision: DecisionTrace
    sdn: SdnTrace
    outcome: OutcomeTrace
    timing: TimingTrace = Field(default_factory=TimingTrace)
    fallback_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_route_constraint_against_inference(self):
        if self.telemetry.drone_id != self.drone_id:
            raise ValueError("telemetry.drone_id must match event drone_id")
        if self.schema_version == "3.1":
            if self.network_security_analysis is None:
                raise ValueError("schema 3.1 requires network_security_analysis")
            evidence = self.telemetry.security_evidence
            network = self.network_security_analysis
            if evidence is not None and evidence.provenance is None:
                raise ValueError("schema 3.1 security evidence requires provenance")
            if network.status != "UNAVAILABLE":
                if evidence is None or evidence.provenance is None:
                    raise ValueError(
                        "available schema 3.1 network analysis requires evidence provenance"
                    )
                if network.evidence_source != evidence.provenance.category:
                    raise ValueError("network analysis evidence source mismatch")
                if network.evidence_independent != evidence.provenance.independent:
                    raise ValueError("network analysis independence mismatch")
                if self.mode == "real" and not network.evidence_independent:
                    raise ValueError("real-mode network analysis requires independent evidence")
        # Version 2.0 events predate the explicit mask and receive compatibility
        # defaults when loaded. New 2.1 events must prove that their constraint
        # state was derived from the scores that actually drove the decision.
        if self.schema_version == "2.0":
            return self
        expected_mask = [
            score <= self.decision.safety_threshold
            for score in self.inference.path_scores
        ]
        if self.decision.safe_action_mask != expected_mask:
            raise ValueError(
                "decision.safe_action_mask must match inference.path_scores "
                "and safety_threshold"
            )
        if self.decision.no_safe_route != (not any(expected_mask)):
            raise ValueError(
                "decision.no_safe_route must match the derived safe-action mask"
            )
        return self
