"""Shared routing reward used by both RL training and live orchestration."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from numbers import Integral
from pathlib import Path
from typing import Mapping, Optional, Sequence

import yaml


_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())

REWARD_DEFINITION = "routing_qos_v2"
SECURE_REWARD_DEFINITION = "routing_security_qos_v3"
PATH_NAMES = ("direct", "satellite", "mesh")
PATH_ENERGY_COSTS = (0.2, 0.6, 0.9)
MAX_LATENCY_MS = (100.0, 300.0, 600.0)


@dataclass(frozen=True)
class RewardWeights:
    throughput: float
    delay: float
    energy: float
    loss: float
    switching: float


@dataclass(frozen=True)
class RewardBreakdown:
    """Inspectable terms whose signed combination produces ``total``."""

    throughput_norm: float
    delay_norm: float
    energy_norm: float
    loss_norm: float
    switched: bool
    throughput_contribution: float
    delay_penalty: float
    energy_penalty: float
    loss_penalty: float
    switching_penalty: float
    total: float
    held: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def weights_from_config(config: Mapping[str, float]) -> RewardWeights:
    weights = RewardWeights(
        throughput=float(config["w_throughput"]),
        delay=float(config["w_delay"]),
        energy=float(config["w_energy"]),
        loss=float(config["w_loss"]),
        switching=float(config.get("w_switch", 0.15)),
    )
    if not all(math.isfinite(value) and value >= 0.0 for value in asdict(weights).values()):
        raise ValueError("reward weights must be finite and non-negative")
    return weights


DEFAULT_REWARD_WEIGHTS = weights_from_config(_RL_CFG["reward"])


def _normalised_triplet(name: str, values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != len(PATH_NAMES):
        raise ValueError(f"{name} must contain exactly {len(PATH_NAMES)} values")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} values must be finite")
    return tuple(min(1.0, max(0.0, value)) for value in result)


def compute_routing_reward(
    action: int,
    path_scores: Sequence[float],
    path_latencies: Sequence[float],
    path_losses: Sequence[float],
    *,
    previous_action: Optional[int] = None,
    weights: RewardWeights = DEFAULT_REWARD_WEIGHTS,
) -> RewardBreakdown:
    """Compute the configured QoS reward from normalized per-path inputs.

    ``previous_action=None`` represents the first decision in an episode/run and
    therefore suppresses the switching penalty.
    """
    if isinstance(action, bool) or not isinstance(action, Integral):
        raise TypeError("action must be an integer path index")
    action = int(action)
    if action < 0 or action >= len(PATH_NAMES):
        raise ValueError(f"action must be in [0, {len(PATH_NAMES) - 1}]")
    if previous_action is not None:
        if isinstance(previous_action, bool) or not isinstance(previous_action, Integral):
            raise TypeError("previous_action must be an integer path index or None")
        previous_action = int(previous_action)
        if previous_action < 0 or previous_action >= len(PATH_NAMES):
            raise ValueError(f"previous_action must be in [0, {len(PATH_NAMES) - 1}]")

    threats = _normalised_triplet("path_scores", path_scores)
    latencies = _normalised_triplet("path_latencies", path_latencies)
    losses = _normalised_triplet("path_losses", path_losses)

    throughput_norm = 1.0 - threats[action]
    delay_norm = latencies[action]
    energy_norm = PATH_ENERGY_COSTS[action]
    loss_norm = losses[action]
    switched = previous_action is not None and action != previous_action

    throughput_contribution = weights.throughput * throughput_norm
    delay_penalty = weights.delay * delay_norm
    energy_penalty = weights.energy * energy_norm
    loss_penalty = weights.loss * loss_norm
    switching_penalty = weights.switching if switched else 0.0
    total = (
        throughput_contribution
        - delay_penalty
        - energy_penalty
        - loss_penalty
        - switching_penalty
    )
    calculated_values = (
        throughput_contribution,
        delay_penalty,
        energy_penalty,
        loss_penalty,
        switching_penalty,
        total,
    )
    if not all(math.isfinite(value) for value in calculated_values):
        raise ValueError("reward calculation produced a non-finite value")

    return RewardBreakdown(
        throughput_norm=throughput_norm,
        delay_norm=delay_norm,
        energy_norm=energy_norm,
        loss_norm=loss_norm,
        switched=switched,
        throughput_contribution=throughput_contribution,
        delay_penalty=delay_penalty,
        energy_penalty=energy_penalty,
        loss_penalty=loss_penalty,
        switching_penalty=switching_penalty,
        total=float(total),
    )


def compute_secure_routing_reward(
    action: int,
    path_scores: Sequence[float],
    path_latencies: Sequence[float],
    path_losses: Sequence[float],
    *,
    previous_action: Optional[int] = None,
    weights: RewardWeights = DEFAULT_REWARD_WEIGHTS,
    hold_penalty: float = 0.35,
) -> RewardBreakdown:
    """v3 reward with an explicit HOLD action at index three.

    HOLD loses availability but avoids forwarding across a route already judged
    unsafe.  It is therefore preferable to highly negative unsafe-route reward,
    but remains worse than a healthy forwarding path.
    """
    if int(action) != len(PATH_NAMES):
        safe_previous = previous_action
        if safe_previous == len(PATH_NAMES):
            safe_previous = None
        return compute_routing_reward(
            action,
            path_scores,
            path_latencies,
            path_losses,
            previous_action=safe_previous,
            weights=weights,
        )
    switched = previous_action is not None and previous_action != action
    switching_penalty = weights.switching if switched else 0.0
    total = -max(0.0, float(hold_penalty)) - switching_penalty
    return RewardBreakdown(
        throughput_norm=0.0,
        delay_norm=0.0,
        energy_norm=0.0,
        loss_norm=0.0,
        switched=switched,
        throughput_contribution=0.0,
        delay_penalty=0.0,
        energy_penalty=0.0,
        loss_penalty=max(0.0, float(hold_penalty)),
        switching_penalty=switching_penalty,
        total=total,
        held=True,
    )
