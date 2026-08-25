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


class TelemetrySnapshot(_StrictModel):
    drone_id: Literal["drone_1", "drone_2", "drone_3"]
    timestamp: float = Field(gt=0.0)
    source: Literal["synthetic", "live", "synthetic_fallback", "legacy_api_fallback"]
    paths: list[PathMetrics] = Field(min_length=3, max_length=3)
    gps: Optional[GpsMetrics] = None
    ew_status: Optional[EwStatus] = None

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
    policy_action_id: int = Field(ge=0, le=2)
    policy_path: Literal["direct", "satellite", "mesh"]
    requested_action_id: int = Field(ge=0, le=2)
    requested_path: Literal["direct", "satellite", "mesh"]
    installed_action_id: Optional[int] = Field(default=None, ge=0, le=2)
    installed_path: Optional[Literal["direct", "satellite", "mesh"]] = None
    threat_level: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    observation: Optional[list[float]] = Field(default=None, min_length=14, max_length=14)
    safety_override: bool = False
    safe_action_mask: list[bool] = Field(
        default_factory=lambda: [True, True, True],
        min_length=3,
        max_length=3,
    )
    no_safe_route: bool = False
    constraint_reason: Optional[
        Literal["policy_route_above_threshold", "all_routes_above_threshold"]
    ] = None
    safety_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    all_unsafe_behavior: Literal["least_risk_route"] = "least_risk_route"
    route_changed: bool = False

    @model_validator(mode="after")
    def validate_constraint_state(self):
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


class OutcomeTrace(_StrictModel):
    reward: float
    reward_definition: Literal["routing_qos_v2"]
    reward_components: RewardComponents
    path: Literal["direct", "satellite", "mesh"]
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


class DecisionEvent(_StrictModel):
    """The authoritative record for one telemetry → decision → SDN cycle."""

    schema_version: Literal["2.0", "2.1"] = "2.1"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step: int = Field(ge=1)
    timestamp: float = Field(gt=0.0)
    drone_id: Literal["drone_1", "drone_2", "drone_3"]
    mode: Literal["simulation", "real"]
    telemetry: TelemetrySnapshot
    inference: InferenceTrace
    decision: DecisionTrace
    sdn: SdnTrace
    outcome: OutcomeTrace
    timing: TimingTrace = Field(default_factory=TimingTrace)
    fallback_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_route_constraint_against_inference(self):
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
