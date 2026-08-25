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

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_TRAIN_CFG = _RL_CFG["training"]
_ENV_CFG = _RL_CFG["environment"]
_SAFETY_CFG = _RL_CFG.get("safety", {})
_SAFETY_THRESHOLD, _ALL_UNSAFE_BEHAVIOR = resolve_safety_config(_SAFETY_CFG)

logger = logging.getLogger(__name__)

PATH_NAMES = list(SHARED_PATH_NAMES)
_MAX_LATENCY = np.array(MAX_LATENCY_MS, dtype=np.float32)


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

    def __init__(self, model_path: Optional[str] = None):
        self.algo = _TRAIN_CFG.get("algo", "dqn").lower()
        self.device = "cpu"  # Keep CPU for fast inference latency

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
                observation_dim=_ENV_CFG["obs_dim"],
                action_count=_ENV_CFG["num_paths"],
                max_episode_steps=_ENV_CFG["max_steps"],
            )

            self.model = DiscreteSACAgent(
                state_dim=_ENV_CFG["obs_dim"],
                action_dim=_ENV_CFG["num_paths"],
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
            from rl.env import DronePathEnv

            if model_path is None:
                model_path = str(_BASE / _RL_CFG["paths"]["model_save"])

            metadata = validate_checkpoint_metadata(
                model_path,
                algorithm=self.algo,
                observation_dim=_ENV_CFG["obs_dim"],
                action_count=_ENV_CFG["num_paths"],
                max_episode_steps=_ENV_CFG["max_steps"],
            )

            # Load model with a dummy env for SB3 compatibility
            env = DronePathEnv()
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
        if self._prev_action is not None:
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
        return obs.astype(np.float32)

    def predict(
        self,
        path_scores: list,
        reward: Optional[float] = None,
        path_latencies: Optional[list] = None,
        path_losses: Optional[list] = None,
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
        obs = self._build_obs(path_scores, path_latencies, path_losses)

        if self.algo == "sac":
            # Discrete SAC predict
            action, _ = self.model.predict(obs, deterministic=True)
        else:
            # DQN predict
            action, _ = self.model.predict(obs, deterministic=True)
        action = int(action)
        original_action = action

        constrained = constrain_route_action(
            action,
            path_scores,
            threat_threshold=_SAFETY_THRESHOLD,
        )
        action = constrained.action_id
        if constrained.safety_override:
            logger.debug(
                "[RLAgent] Critical threat on chosen path — failing over to safest %s",
                PATH_NAMES[action],
            )
        elif constrained.no_safe_route:
            logger.debug(
                "[RLAgent] All routes exceed the safety threshold; using least-risk %s",
                PATH_NAMES[action],
            )

        # Update running state
        self._prev_action = action
        self._step = (self._step + 1) % self._max_steps

        return {
            "action_id": action,
            "path_name": PATH_NAMES[action],
            "threat_level": _threat_level(path_scores),
            "decision_source": "rl_model",
            "observation": obs.tolist(),
            "safety_override": constrained.safety_override,
            "original_action_id": original_action,
            "safe_action_mask": list(constrained.safe_action_mask),
            "no_safe_route": constrained.no_safe_route,
            "constraint_reason": constrained.constraint_reason,
            "safety_threshold": constrained.threat_threshold,
            "all_unsafe_behavior": constrained.all_unsafe_behavior,
        }

    def reset(self) -> None:
        """Reset stateful trackers at episode boundaries."""
        self._prev_action = None
        self._prev_reward = 0.0
        self._step = 0
