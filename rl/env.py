"""
rl/env.py — Upgraded Gymnasium Environment  [FLARE v2]

Custom Gymnasium environment for anti-jamming path selection.

Observation space (14-dim Box):
  [path0_threat, path1_threat, path2_threat,       # FL threat scores  (3)
   path0_latency, path1_latency, path2_latency,    # Normalised latency (3)
   path0_loss, path1_loss, path2_loss,             # Normalised packet loss (3)
   prev_action_0, prev_action_1, prev_action_2,    # One-hot previous action (3)
   prev_reward,                                    # Previous step reward (1)
   step_norm]                                      # Normalised step counter (1)

Action space: Discrete(3) — 0=direct, 1=satellite, 2=mesh

Reward Function:
  reward = w1*throughput_norm - w2*delay_norm - w3*energy_norm - w4*loss_norm - w_switch*I(switch)
  All terms normalised to [0, 1]. Weights from config/rl_config.yaml.

Classes:
  DronePathEnv          — Synthetic random RF state (upgraded to 14-dim)
  RealDataDronePathEnv  — Replays preprocessed test.csv (upgraded to 14-dim)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import gymnasium as gym
import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_ENV_CFG = _RL_CFG["environment"]
_REW_CFG = _RL_CFG["reward"]

PATH_NAMES = ["direct", "satellite", "mesh"]

# Normalised energy cost per path proxy: direct < satellite < mesh
_ENERGY_COST = np.array([0.2, 0.6, 0.9], dtype=np.float32)

# Max latency per path for normalisation (ms)
_MAX_LATENCY = np.array([100.0, 300.0, 600.0], dtype=np.float32)


# ---------------------------------------------------------------------------
# Drone Path Environment (Synthetic/Default Training Mode)
# ---------------------------------------------------------------------------

class DronePathEnv(gym.Env):
    """
    Upgraded Gymnasium Environment with 14-dim observation space.
    Exposes links quality characteristics and incorporates switching penalties
    to prevent route-switching packet oscillation.
    """

    metadata = {"render_modes": ["human"]}

    def __init__(self, render_mode: Optional[str] = None):
        super().__init__()
        self.render_mode = render_mode
        self.max_steps = _ENV_CFG["max_steps"]
        self.lat_window = _ENV_CFG["latency_window"]
        self.obs_dim = _ENV_CFG["obs_dim"]          # 14
        self.num_paths = _ENV_CFG["num_paths"]      # 3

        # Reward weights
        self.w1 = float(_REW_CFG["w_throughput"])
        self.w2 = float(_REW_CFG["w_delay"])
        self.w3 = float(_REW_CFG["w_energy"])
        self.w4 = float(_REW_CFG["w_loss"])
        self.w_switch = float(_REW_CFG.get("w_switch", 0.15))

        # Spaces
        self.observation_space = gym.spaces.Box(
            low=np.array([0.0] * 12 + [-np.inf, 0.0], dtype=np.float32),
            high=np.array([1.0] * 12 + [np.inf, 1.0], dtype=np.float32),
            dtype=np.float32,
        )
        self.action_space = gym.spaces.Discrete(self.num_paths)

        # Environment State Variables
        self._path_scores: np.ndarray = np.zeros(3, dtype=np.float32)      # threat scores
        self._path_latencies: np.ndarray = np.zeros(3, dtype=np.float32)   # normalised latencies
        self._path_losses: np.ndarray = np.zeros(3, dtype=np.float32)      # normalised losses
        self._prev_action: int = 0
        self._prev_reward: float = 0.0
        self._step: int = 0
        self._jammed_paths: np.ndarray = np.zeros(3, dtype=bool)

    def _build_obs(self) -> np.ndarray:
        """Assemble the 14-dimensional state observation."""
        prev_action_onehot = np.zeros(3, dtype=np.float32)
        prev_action_onehot[self._prev_action] = 1.0

        step_norm = np.array([self._step / self.max_steps], dtype=np.float32)

        obs = np.concatenate([
            self._path_scores,       # [3]
            self._path_latencies,    # [3]
            self._path_losses,       # [3]
            prev_action_onehot,      # [3]
            [self._prev_reward],     # [1]
            step_norm,               # [1]
        ])
        return obs.astype(np.float32)

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        self._path_scores = self.np_random.uniform(0.0, 0.3, size=3).astype(np.float32)

        # Initialise latencies and losses consistent with scores
        for i in range(3):
            self._path_latencies[i] = float(self._get_latency_norm(i, self._path_scores[i]))
            self._path_losses[i] = float(np.clip(self._path_scores[i] * 0.8 + self.np_random.uniform(0.0, 0.05), 0.0, 1.0))

        self._prev_action = 0
        self._prev_reward = 0.0
        self._step = 0
        self._jammed_paths = np.zeros(3, dtype=bool)

        obs = self._build_obs()
        return obs, {}

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict]:
        assert self.action_space.contains(action), f"Invalid action {action}"

        # Evolve RF state (only in default training/synthetic mode)
        self._evolve_rf_state()

        # Compute reward
        reward = self._compute_reward(action)

        # Update environment state trackers
        self._prev_action = action
        self._prev_reward = reward
        self._step += 1

        obs = self._build_obs()
        terminated = self._step >= self.max_steps
        info = {
            "path_name": PATH_NAMES[action],
            "reward": reward,
            "jammed_paths": self._jammed_paths.tolist(),
            "path_scores": self._path_scores.tolist(),
            "latencies": self._path_latencies.tolist(),
            "packet_losses": self._path_losses.tolist(),
            "step": self._step,
        }

        if self.render_mode == "human":
            self._render_human(action, reward)

        return obs, reward, terminated, False, info

    def inject_threat_scores(self, path_scores: list) -> None:
        """
        External injection of threat scores (orchestrator mode).
        Dynamically updates associated latencies and packet losses.
        """
        self._path_scores = np.clip(np.array(path_scores, dtype=np.float32), 0.0, 1.0)
        # Update associated latencies and losses consistently
        for i in range(3):
            self._path_latencies[i] = float(self._get_latency_norm(i, self._path_scores[i]))
            # Loss = threat * 0.8 + small noise
            self._path_losses[i] = float(np.clip(self._path_scores[i] * 0.8, 0.0, 1.0))

    def _evolve_rf_state(self) -> None:
        """Evolve synthetic RF environment conditions (20% jam chance)."""
        self._jammed_paths = self.np_random.random(3) < 0.2
        for i, jammed in enumerate(self._jammed_paths):
            if jammed:
                self._path_scores[i] = float(self.np_random.uniform(0.6, 1.0))
            else:
                self._path_scores[i] = float(self.np_random.uniform(0.0, 0.3))

            # Recalculate latency/loss
            self._path_latencies[i] = float(self._get_latency_norm(i, self._path_scores[i]))
            loss_noise = self.np_random.uniform(0.0, 0.1)
            self._path_losses[i] = float(np.clip(self._path_scores[i] * 0.8 + loss_noise, 0.0, 1.0))

    def _get_latency_norm(self, path_idx: int, threat: float) -> float:
        """Compute normalised latency (0=best, 1=worst)."""
        base_lat = _MAX_LATENCY[path_idx] * (0.1 + 0.9 * threat)
        return float(np.clip(base_lat / _MAX_LATENCY[path_idx], 0.0, 1.0))

    def _compute_reward(self, action: int) -> float:
        """
        reward = w1*throughput_norm - w2*delay_norm - w3*energy_norm - w4*loss_norm - w_switch*I(switch)
        """
        threat = float(self._path_scores[action])

        # QoS components
        throughput_norm = 1.0 - threat
        delay_norm = float(self._path_latencies[action])
        energy_norm = float(_ENERGY_COST[action])
        loss_norm = float(self._path_losses[action])

        # Base reward
        reward = (
            self.w1 * throughput_norm
            - self.w2 * delay_norm
            - self.w3 * energy_norm
            - self.w4 * loss_norm
        )

        # Switching penalty (prevents high-frequency route oscillation)
        if self._step > 0 and action != self._prev_action:
            reward -= self.w_switch

        return float(reward)

    def _render_human(self, action: int, reward: float) -> None:
        print(
            f"[ENV] step={self._step:4d} | "
            f"threats=[{','.join(f'{s:.2f}' for s in self._path_scores)}] | "
            f"action={PATH_NAMES[action]:9s} | reward={reward:+.3f}"
        )


# ---------------------------------------------------------------------------
# Real Data Drone Path Environment (Replay Mode)
# ---------------------------------------------------------------------------

class RealDataDronePathEnv(DronePathEnv):
    """
    Replays RF characteristics from a real preprocessed dataset CSV,
    scaled to the 14-dimensional state representation.
    """

    def __init__(
        self,
        csv_path: Optional[Path] = None,
        render_mode: Optional[str] = None,
    ):
        super().__init__(render_mode=render_mode)
        if csv_path is None:
            configured = _RL_CFG.get("paths", {}).get("real_data_csv", "datasets/processed/test.csv")
            csv_path = _BASE / configured
        self._csv_path = csv_path
        self._df: Optional[pd.DataFrame] = None
        self._row_idx: int = 0
        self._window_start: int = 0
        self._loaded = False

    def _load_data(self) -> None:
        if self._loaded:
            return
        if not self._csv_path.exists():
            raise FileNotFoundError(
                f"Real data CSV not found: {self._csv_path}\n"
                "Run: python datasets/preprocess.py"
            )
        self._df = pd.read_csv(self._csv_path)
        required = ["rssi", "pdr", "sinr", "latency", "packet_loss", "jammed"]
        available = [c for c in required if c in self._df.columns]
        self._df = self._df[available].dropna().reset_index(drop=True)
        self._loaded = True
        logger.info(
            "RealDataDronePathEnv loaded %d rows from %s",
            len(self._df), self._csv_path.name,
        )

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> Tuple[np.ndarray, dict]:
        self._load_data()
        super().reset(seed=seed, options=options)

        n = len(self._df)
        max_start = max(0, n - self.max_steps - 1)
        self._window_start = int(self.np_random.integers(0, max_start + 1))
        self._row_idx = self._window_start
        self._step = 0

        self._apply_real_row()
        obs = self._build_obs()
        return obs, {}

    def _apply_real_row(self) -> None:
        """Read and scale current CSV row to 14-dim observation fields."""
        if self._df is None or self._row_idx >= len(self._df):
            self._row_idx = self._window_start

        row = self._df.iloc[self._row_idx]
        jammed_val = float(row.get("jammed", 0))
        sinr_norm = float(row.get("sinr", 0.5))

        base_threat = jammed_val * (1.0 - sinr_norm) + (1.0 - jammed_val) * (sinr_norm * 0.1)

        # Distribute threat and link features with small random path variations
        noise = self.np_random.uniform(-0.05, 0.05, size=3).astype(np.float32)
        self._path_scores = np.clip(np.array([base_threat] * 3, dtype=np.float32) + noise, 0.0, 1.0)
        self._jammed_paths = self._path_scores > 0.5

        # Latencies & Packet losses from dataset
        lat_val = float(row.get("latency", 0.2))
        loss_val = float(row.get("packet_loss", 0.1))

        self._path_latencies = np.clip(np.array([lat_val] * 3, dtype=np.float32) + noise * 0.1, 0.0, 1.0)
        self._path_losses = np.clip(np.array([loss_val] * 3, dtype=np.float32) + noise * 0.1, 0.0, 1.0)

        self._row_idx += 1

    def _evolve_rf_state(self) -> None:
        """Override evolve method to draw next row from preprocessed dataset."""
        self._apply_real_row()


# ---------------------------------------------------------------------------
# Digital Twin Environment (Predictive Mirror Mode)
# ---------------------------------------------------------------------------

class DigitalTwinEnv(DronePathEnv):
    """
    High-fidelity virtual replica environment for the RL Agent.
    Subscribes to the SwarmStateRegistry.
    reset() pulls the exact current real-world state.
    step() uses analytical predictive models to simulate the future.
    """

    def __init__(self, render_mode: Optional[str] = None):
        super().__init__(render_mode=render_mode)
        # Initialize and start Twin synchronization
        from simulation.digital_twin import twin_registry
        self.registry = twin_registry
        self.registry.start_sync()
        self.target_drone = "drone_1"
        self._current_twin_state = {}

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed, options=options)

        # 1. Sync Phase: Pull physical state
        self._current_twin_state = self.registry.get_state(self.target_drone)

        # Apply physical RF metrics to the Gym state variables
        metrics = self._current_twin_state.get("metrics")
        if metrics:
            for i, p in enumerate(metrics):
                if p["path_id"] == "direct":
                    self._path_latencies[0] = min(1.0, p["latency"] / _MAX_LATENCY[0])
                    self._path_losses[0] = p["packet_loss"]
                elif p["path_id"] == "satellite":
                    self._path_latencies[1] = min(1.0, p["latency"] / _MAX_LATENCY[1])
                    self._path_losses[1] = p["packet_loss"]
                elif p["path_id"] == "mesh":
                    self._path_latencies[2] = min(1.0, p["latency"] / _MAX_LATENCY[2])
                    self._path_losses[2] = p["packet_loss"]

        # Apply battery as a proxy to threat scaling (incorporating energy state)
        battery = self._current_twin_state.get("battery", 10000.0)
        energy_penalty = 1.0 - (battery / 10000.0)
        self._path_scores += energy_penalty * 0.1

        self._step = 0
        obs = self._build_obs()
        return obs, {"sync_latency_ms": self.registry.sync_latency_ms}

    def _evolve_rf_state(self) -> None:
        """
        In Digital Twin mode, evolve uses the predictive kinematics model
        rather than pulling random/preprocessed dataset rows.
        """
        # We predict what the next state WILL be given the current EW threats
        next_state = self.registry.predict_next_state(self._current_twin_state, PATH_NAMES[self._prev_action])
        self._current_twin_state = next_state

        metrics = self._current_twin_state.get("metrics")
        if metrics:
            for i, p in enumerate(metrics):
                if p["path_id"] == "direct":
                    self._path_latencies[0] = min(1.0, p["latency"] / _MAX_LATENCY[0])
                    self._path_losses[0] = p["packet_loss"]
                elif p["path_id"] == "satellite":
                    self._path_latencies[1] = min(1.0, p["latency"] / _MAX_LATENCY[1])
                    self._path_losses[1] = p["packet_loss"]
                elif p["path_id"] == "mesh":
                    self._path_latencies[2] = min(1.0, p["latency"] / _MAX_LATENCY[2])
                    self._path_losses[2] = p["packet_loss"]


# ---------------------------------------------------------------------------
# Self-Test Hook
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    env = DigitalTwinEnv()
    o, info = env.reset()
    assert o.shape == (14,), f"Expected shape (14,), got {o.shape}"
    print(f"DigitalTwinEnv self-test passed. Sync Latency: {info['sync_latency_ms']:.2f} ms")
