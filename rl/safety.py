"""Deterministic runtime constraints for three-path routing decisions.

The RL policy can choose only routes that the SDN layer can actually install.
This module therefore keeps the three-action contract and represents a fully
degraded network as an explicit ``no_safe_route`` state.  In that state the
least-threatened route is still requested so connectivity is preserved, but
callers can surface that the installed route is not considered safe.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from rl.reward import PATH_NAMES


DEFAULT_THREAT_THRESHOLD = 0.8
ALL_UNSAFE_BEHAVIOR = "least_risk_route"


def resolve_safety_config(
    config: Mapping[str, Any] | None,
) -> tuple[float, str]:
    """Validate the supported runtime safety configuration."""
    values = config or {}
    threshold = float(values.get("threat_threshold", DEFAULT_THREAT_THRESHOLD))
    if not math.isfinite(threshold) or threshold < 0.0 or threshold > 1.0:
        raise ValueError("safety.threat_threshold must be a finite value in [0, 1]")
    behavior = str(values.get("all_unsafe_behavior", ALL_UNSAFE_BEHAVIOR))
    if behavior != ALL_UNSAFE_BEHAVIOR:
        raise ValueError(
            "safety.all_unsafe_behavior must be 'least_risk_route'; "
            "the SDN layer does not implement another degraded action"
        )
    return threshold, behavior


@dataclass(frozen=True)
class ConstrainedRouteDecision:
    """Result of applying the runtime route-safety constraint."""

    policy_action_id: int
    action_id: int
    safe_action_mask: tuple[bool, ...]
    safety_override: bool
    no_safe_route: bool
    constraint_reason: str | None
    threat_threshold: float
    all_unsafe_behavior: str = ALL_UNSAFE_BEHAVIOR

    def as_dict(self) -> dict:
        return {
            "policy_action_id": self.policy_action_id,
            "action_id": self.action_id,
            "safe_action_mask": list(self.safe_action_mask),
            "safety_override": self.safety_override,
            "no_safe_route": self.no_safe_route,
            "constraint_reason": self.constraint_reason,
            "safety_threshold": self.threat_threshold,
            "all_unsafe_behavior": self.all_unsafe_behavior,
        }


def validate_path_scores(path_scores: Sequence[float]) -> tuple[float, ...]:
    """Return validated route scores in canonical direct/satellite/mesh order."""
    if len(path_scores) != len(PATH_NAMES):
        raise ValueError(
            f"path_scores must contain exactly {len(PATH_NAMES)} values"
        )
    scores = tuple(float(score) for score in path_scores)
    if any(not math.isfinite(score) or score < 0.0 or score > 1.0 for score in scores):
        raise ValueError("path_scores must contain finite values in [0, 1]")
    return scores


def constrain_route_action(
    policy_action_id: int,
    path_scores: Sequence[float],
    *,
    threat_threshold: float = DEFAULT_THREAT_THRESHOLD,
) -> ConstrainedRouteDecision:
    """Apply the executable three-route safety policy.

    A route is safe when its threat score is less than or equal to the configured
    threshold.  An unsafe learned action is replaced with the lowest-threat safe
    route when one exists.  If every route is unsafe, the lowest-threat route is
    selected as an availability-preserving degraded fallback and
    ``no_safe_route`` is set independently of whether the action changed.
    """
    scores = validate_path_scores(path_scores)
    if isinstance(policy_action_id, bool) or not isinstance(policy_action_id, int):
        raise ValueError("policy_action_id must be an integer")
    if policy_action_id < 0 or policy_action_id >= len(PATH_NAMES):
        raise ValueError(f"policy_action_id must be in [0, {len(PATH_NAMES) - 1}]")
    threshold = float(threat_threshold)
    if not math.isfinite(threshold) or threshold < 0.0 or threshold > 1.0:
        raise ValueError("threat_threshold must be a finite value in [0, 1]")

    safe_action_mask = tuple(score <= threshold for score in scores)
    safe_actions = [
        action_id
        for action_id, is_safe in enumerate(safe_action_mask)
        if is_safe
    ]

    if not safe_actions:
        action_id = min(range(len(scores)), key=scores.__getitem__)
        no_safe_route = True
        constraint_reason = "all_routes_above_threshold"
    elif safe_action_mask[policy_action_id]:
        action_id = policy_action_id
        no_safe_route = False
        constraint_reason = None
    else:
        action_id = min(safe_actions, key=scores.__getitem__)
        no_safe_route = False
        constraint_reason = "policy_route_above_threshold"

    return ConstrainedRouteDecision(
        policy_action_id=policy_action_id,
        action_id=action_id,
        safe_action_mask=safe_action_mask,
        safety_override=action_id != policy_action_id,
        no_safe_route=no_safe_route,
        constraint_reason=constraint_reason,
        threat_threshold=threshold,
    )
