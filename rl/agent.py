"""
rl/agent.py — Upgraded RL Inference Wrapper  [FLARE v2]

Loads and wraps either the standard DQN model (from SB3) or the
custom Discrete SAC model (from PyTorch) for real-time routing predictions.

Exposes a clean, stateful, backward-compatible `predict()` interface.
Supports 14-dimensional link characteristics observations.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import yaml

from rl.checkpoint import validate_checkpoint_metadata
from rl.reward import MAX_LATENCY_MS, PATH_NAMES as SHARED_PATH_NAMES
from rl.safety import constrain_route_action, resolve_safety_config, validate_path_scores
from schemas.contracts import (
    ROUTING_ACTIONS_V3,
    ROUTING_STATE_V2,
    ROUTING_STATE_V3,
    routing_contract,
)

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_TRAIN_CFG = _RL_CFG["training"]
_ENV_CFG = _RL_CFG["environment"]
_SAFETY_CFG = _RL_CFG.get("safety", {})
_SAFETY_THRESHOLD, _ALL_UNSAFE_BEHAVIOR = resolve_safety_config(_SAFETY_CFG)

logger = logging.getLogger(__name__)

PATH_NAMES = list(SHARED_PATH_NAMES)
_MAX_LATENCY = np.array(MAX_LATENCY_MS, dtype=np.float32)
_OBSERVATION_NAMES_V2 = (
    "threat_direct", "threat_satellite", "threat_mesh",
    "latency_direct", "latency_satellite", "latency_mesh",
    "loss_direct", "loss_satellite", "loss_mesh",
    "previous_action_direct", "previous_action_satellite", "previous_action_mesh",
    "previous_reward", "step_fraction",
)
_OBSERVATION_NAMES_V3 = (
    *_OBSERVATION_NAMES_V2,
    "client_trust", "insider_risk", "containment_score", "evidence_freshness",
)


def _threat_level(scores: list) -> str:
    max_score = max(scores)
    if max_score >= 0.7:
        return "HIGH"
    elif max_score >= 0.4:
        return "MEDIUM"
    return "LOW"


class RLAgent:
    """
    Stateful inference wrapper for the trained routing agents.
    Supports both "dqn" (standard SB3 DQN) and "sac" (Discrete SAC).
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        *,
        routing_contract_name: Optional[str] = None,
    ):
        self.algo = _TRAIN_CFG.get("algo", "dqn").lower()
        self.device = "cpu"  # Keep CPU for fast inference latency
        self.routing_contract = routing_contract_name or ROUTING_STATE_V2
        contract = routing_contract(self.routing_contract)

        # State tracking for stateful observation assembly
        self._prev_action: Optional[int] = None
        self._prev_reward: float = 0.0
        self._step: int = 0
        self._max_steps: int = _ENV_CFG["max_steps"]

        if self.algo == "sac":
            # Load PyTorch Discrete SAC
            from rl.sac_discrete import DiscreteSACAgent
            if model_path is None:
                model_path = str(_BASE / _RL_CFG["paths"].get("sac_model_save", "models/rl_sac_model.pth"))

            metadata = validate_checkpoint_metadata(
                model_path,
                algorithm=self.algo,
                observation_dim=contract.observation_dim,
                action_count=len(contract.actions),
                max_episode_steps=_ENV_CFG["max_steps"],
                observation_definition=self.routing_contract,
            )

            self.model = DiscreteSACAgent(
                state_dim=contract.observation_dim,
                action_dim=len(contract.actions),
                device=self.device,
            )
            try:
                self.model.load(model_path)
            except Exception as exc:
                raise RuntimeError(
                    f"failed to load Discrete SAC model from {model_path}"
                ) from exc
            logger.info(
                "Loaded Discrete SAC model from %s (reward=%s)",
                model_path,
                metadata["reward_definition"],
            )
        else:
            # Load Stable-Baselines3 DQN
            from stable_baselines3 import DQN
            from rl.env import DronePathEnv, TrustAwareDronePathEnv

            if model_path is None:
                model_path = str(_BASE / _RL_CFG["paths"]["model_save"])

            metadata = validate_checkpoint_metadata(
                model_path,
                algorithm=self.algo,
                observation_dim=contract.observation_dim,
                action_count=len(contract.actions),
                max_episode_steps=_ENV_CFG["max_steps"],
                observation_definition=self.routing_contract,
            )

            # Load model with a dummy env for SB3 compatibility
            env = TrustAwareDronePathEnv() if self.routing_contract == ROUTING_STATE_V3 else DronePathEnv()
            self.model = DQN.load(model_path, env=env)
            logger.info(
                "Loaded DQN model from %s (reward=%s)",
                model_path,
                metadata["reward_definition"],
            )

    def _get_latency_norm(self, path_idx: int, threat: float) -> float:
        base_lat = _MAX_LATENCY[path_idx] * (0.1 + 0.9 * threat)
        return float(np.clip(base_lat / _MAX_LATENCY[path_idx], 0.0, 1.0))

    def _build_obs(
        self,
        path_scores: list,
        path_latencies: Optional[list] = None,
        path_losses: Optional[list] = None,
        client_trust: float = 1.0,
        insider_risk: float = 0.0,
        containment_score: float = 0.0,
        evidence_freshness: float = 1.0,
    ) -> np.ndarray:
        """Assemble the 14-dimensional observation vector."""
        scores = np.array(validate_path_scores(path_scores), dtype=np.float32)

        # Estimate latencies/losses if not explicitly provided (backward compatibility)
        if path_latencies is None:
            path_latencies = [self._get_latency_norm(i, float(s)) for i, s in enumerate(scores)]
        if path_losses is None:
            path_losses = [float(s) * 0.8 for s in scores]

        latencies = np.clip(np.array(path_latencies, dtype=np.float32), 0.0, 1.0)
        losses = np.clip(np.array(path_losses, dtype=np.float32), 0.0, 1.0)

        prev_action_onehot = np.zeros(3, dtype=np.float32)
        # The v3 HOLD action has no route slot in the versioned three-route
        # history vector. Represent it as all-zero rather than indexing slot 3.
        if self._prev_action is not None and self._prev_action < len(prev_action_onehot):
            prev_action_onehot[self._prev_action] = 1.0

        step_norm = np.array([self._step / self._max_steps], dtype=np.float32)

        obs = np.concatenate([
            scores,                  # [3]
            latencies,               # [3]
            losses,                  # [3]
            prev_action_onehot,      # [3]
            [self._prev_reward],     # [1]
            step_norm,               # [1]
        ])
        if getattr(self, "routing_contract", ROUTING_STATE_V2) == ROUTING_STATE_V3:
            obs = np.concatenate([
                obs,
                np.clip(np.asarray([
                    client_trust,
                    insider_risk,
                    containment_score,
                    evidence_freshness,
                ], dtype=np.float32), 0.0, 1.0),
            ])
        return obs.astype(np.float32)

    def predict(
        self,
        path_scores: list,
        reward: Optional[float] = None,
        path_latencies: Optional[list] = None,
        path_losses: Optional[list] = None,
        client_trust: float = 1.0,
        insider_risk: float = 0.0,
        containment_score: float = 0.0,
        evidence_freshness: float = 1.0,
    ) -> dict:
        """
        Predict the best routing action.

        Args:
            path_scores:    Threat probabilities [3] in [0,1].
            reward:         Previous step reward for state tracking.
            path_latencies: Optional real link latency norms [3].
            path_losses:    Optional real link loss norms [3].

        Returns:
            Executable action/path, original policy action, threat level, and
            the complete runtime-constraint/no-safe-route state.
        """
        # `reward` represents the reward from the previous decision and must be
        # present in the observation used for this prediction, not the next one.
        if reward is not None:
            self._prev_reward = float(reward)
        obs = self._build_obs(
            path_scores,
            path_latencies,
            path_losses,
            client_trust,
            insider_risk,
            containment_score,
            evidence_freshness,
        )

        if self.algo == "sac":
            # Discrete SAC predict
            action, _ = self.model.predict(obs, deterministic=True)
        else:
            # DQN predict
            action, _ = self.model.predict(obs, deterministic=True)
        action = int(action)
        original_action = action

        contract_name = getattr(self, "routing_contract", ROUTING_STATE_V2)
        v3 = contract_name == ROUTING_STATE_V3
        containment_risk = max(float(insider_risk), 1.0 - float(client_trust), float(containment_score))
        if v3 and containment_risk >= float(
            _RL_CFG.get("routing_v3", {}).get("containment_hold_threshold", 0.72)
        ):
            # Deterministic safety gate: learned routing cannot forward a
            # quarantined participant even if its Q estimate is stale.
            action = 3
        security_override = action != original_action

        constrained = constrain_route_action(
            action,
            path_scores,
            threat_threshold=_SAFETY_THRESHOLD,
            all_unsafe_behavior=(
                _RL_CFG.get("routing_v3", {}).get("all_unsafe_behavior", "hold")
                if v3 else _ALL_UNSAFE_BEHAVIOR
            ),
            allow_hold_action=v3,
        )
        action = constrained.action_id
        if constrained.safety_override:
            logger.debug(
                "[RLAgent] Critical threat on chosen path — failing over to safest %s",
                ROUTING_ACTIONS_V3[action] if v3 else PATH_NAMES[action],
            )
        elif constrained.no_safe_route:
            logger.debug(
                "[RLAgent] All routes exceed the safety threshold; using least-risk %s",
                ROUTING_ACTIONS_V3[action] if v3 else PATH_NAMES[action],
            )

        # Update running state
        self._prev_action = action
        self._step = (self._step + 1) % self._max_steps

        action_names = ROUTING_ACTIONS_V3 if v3 else tuple(PATH_NAMES)
        return {
            "action_id": action,
            "path_name": action_names[action],
            "threat_level": _threat_level(path_scores),
            "decision_source": "rl_model",
            "observation": obs.tolist(),
            "safety_override": constrained.safety_override or security_override,
            "original_action_id": original_action,
            "safe_action_mask": list(constrained.safe_action_mask),
            "no_safe_route": constrained.no_safe_route,
            "constraint_reason": (
                constrained.constraint_reason
                or ("containment_required" if security_override else None)
            ),
            "security_override": security_override,
            "safety_threshold": constrained.threat_threshold,
            "all_unsafe_behavior": constrained.all_unsafe_behavior,
            "network_action": constrained.network_action,
            "routing_contract": contract_name,
            "security_context": {
                "client_trust": float(np.clip(client_trust, 0.0, 1.0)),
                "insider_risk": float(np.clip(insider_risk, 0.0, 1.0)),
                "containment_score": float(np.clip(containment_score, 0.0, 1.0)),
                "evidence_freshness": float(np.clip(evidence_freshness, 0.0, 1.0)),
            },
        }

    def reset(self) -> None:
        """Reset stateful trackers at episode boundaries."""
        self._prev_action = None
        self._prev_reward = 0.0
        self._step = 0

    def explain(self, observation: list[float], action_id: int) -> dict:
        """Explain one standard-DQN policy action from its exact observation."""
        if self.algo != "dqn":
            raise ValueError("routing Q-value explanations are available only for DQN")
        contract = routing_contract(self.routing_contract)
        values = np.asarray(observation, dtype=np.float32)
        if values.shape != (contract.observation_dim,) or not np.isfinite(values).all():
            raise ValueError(
                f"{self.routing_contract} explanation requires "
                f"{contract.observation_dim} finite values"
            )
        if action_id < 0 or action_id >= len(contract.actions):
            raise ValueError("action_id is outside the routing contract")
        import torch
        from fl.explainability import integrated_gradients_q_values
        from schemas.contracts import ROUTING_EXPLANATION_CONTRACT

        result = integrated_gradients_q_values(
            self.model.q_net,
            torch.tensor(values, dtype=torch.float32),
            action_id=int(action_id),
            steps=32,
        )
        names = (
            _OBSERVATION_NAMES_V3
            if self.routing_contract == ROUTING_STATE_V3
            else _OBSERVATION_NAMES_V2
        )
        attributions = result.pop("attributions")
        result.update({
            "contract": ROUTING_EXPLANATION_CONTRACT,
            "routing_contract": self.routing_contract,
            "action_id": int(action_id),
            "action": contract.actions[action_id],
            "feature_attributions": [
                {"feature": name, "signed_attribution": float(attributions[index])}
                for index, name in enumerate(names)
            ],
            "faithfulness_passed": bool(
                np.isfinite(result["normalized_completeness_error"])
                and result["normalized_completeness_error"] <= 0.20
            ),
        })
        return result
