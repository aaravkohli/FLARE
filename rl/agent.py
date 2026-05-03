"""
rl/agent.py — [REAL]
Inference wrapper for the trained DQN agent.

Loads rl_model.zip and provides a clean predict() interface
consumed by the orchestrator and API server.

Usage:
    from rl.agent import RLAgent
    agent = RLAgent()
    result = agent.predict([0.9, 0.2, 0.1])
    # → {"action_id": 1, "path_name": "satellite", "threat_level": "HIGH"}
"""

import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path when running as `python rl/agent.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import yaml

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())

logger = logging.getLogger(__name__)

PATH_NAMES = ["direct", "satellite", "mesh"]


def _threat_level(scores: list) -> str:
    max_score = max(scores)
    if max_score >= 0.7:
        return "HIGH"
    elif max_score >= 0.4:
        return "MEDIUM"
    return "LOW"


class RLAgent:
    """
    Wraps the trained DQN model for inference.
    Handles model loading, observation assembly, and action decoding.
    """

    def __init__(self, model_path: Optional[str] = None):
        from stable_baselines3 import DQN
        from rl.env import DronePathEnv

        if model_path is None:
            model_path = str(_BASE / _RL_CFG["paths"]["model_save"])

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"RL model not found at {model_path}. "
                "Run `python rl/train.py` first."
            )

        # Load with a dummy environment (needed for SB3)
        env = DronePathEnv()
        self.model = DQN.load(model_path, env=env)
        self._env = env
        logger.info("RL agent loaded from %s", model_path)

        # State tracking for observation assembly
        self._prev_action: int = 0
        self._lat_history: list = [0.0] * _RL_CFG["environment"]["latency_window"]
        self._prev_reward: float = 0.0
        self._step: int = 0
        self._max_steps: int = _RL_CFG["environment"]["max_steps"]

    def _build_obs(self, path_scores: list) -> np.ndarray:
        """Build the full 11-dim observation from current path scores."""
        scores = np.clip(np.array(path_scores, dtype=np.float32), 0.0, 1.0)

        prev_action_onehot = np.zeros(3, dtype=np.float32)
        prev_action_onehot[self._prev_action] = 1.0

        lat_hist = np.array(self._lat_history, dtype=np.float32)
        step_norm = np.array([self._step / self._max_steps], dtype=np.float32)

        obs = np.concatenate([scores, prev_action_onehot, lat_hist, [self._prev_reward], step_norm])
        return obs.astype(np.float32)

    def predict(self, path_scores: list, reward: float = 0.0) -> dict:
        """
        Predict the best communication path given FL threat scores.

        Args:
            path_scores: List of 3 floats [direct, satellite, mesh] (0=safe, 1=jammed)
            reward:      Previous step reward (for stateful observation)

        Returns:
            dict with action_id, path_name, threat_level
        """
        obs = self._build_obs(path_scores)
        action, _ = self.model.predict(obs, deterministic=True)
        action = int(action)

        # Safety Bound Override: If the RL agent's chosen path is critically jammed
        # (> 0.8 threat), force a failover to the safest available path.
        # This guarantees autonomous recovery even if DQN exploration was insufficient.
        if path_scores[action] > 0.8:
            action = int(np.argmin(path_scores))
            logger.info("RL safety override: switching to %s (threats: %s)", PATH_NAMES[action], path_scores)

        # Update state for next call
        self._prev_action = action
        self._prev_reward = reward
        self._step = (self._step + 1) % self._max_steps
        # Update latency history (proxy: higher threat → higher latency)
        lat_norm = float(np.clip(path_scores[action], 0.0, 1.0))
        self._lat_history.pop(0)
        self._lat_history.append(lat_norm)

        return {
            "action_id":    action,
            "path_name":    PATH_NAMES[action],
            "threat_level": _threat_level(path_scores),
        }

    def reset(self) -> None:
        """Reset stateful observation tracking (call at episode boundaries)."""
        self._prev_action = 0
        self._lat_history = [0.0] * _RL_CFG["environment"]["latency_window"]
        self._prev_reward = 0.0
        self._step = 0
