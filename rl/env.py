"""
rl/env.py — [REAL]
Custom Gymnasium environment for anti-jamming path selection.

Observation space (11-dim Box):
  [path1_score, path2_score, path3_score,   # FL threat scores  (3)
   prev_action_0, prev_action_1, prev_act_2, # one-hot prev action (3)
   lat_t-1, lat_t-2, lat_t-3,               # latency history   (3)
   prev_reward]                              # previous reward    (1)
  + step_norm                               # normalised step    (1)

Action space: Discrete(3) — 0=direct, 1=satellite, 2=mesh

Reward:
  reward = w1*throughput_norm - w2*delay_norm - w3*energy_norm - w4*loss_norm
  All terms normalised to [0, 1]. Weights from config/rl_config.yaml.

Classes:
  DronePathEnv          — synthetic random RF state (original, backward-compatible)
  RealDataDronePathEnv  — replays rows from datasets/processed/test.csv
"""

from pathlib import Path
from typing import Optional, Tuple

import gymnasium as gym
import numpy as np
import pandas as pd
import yaml

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_ENV_CFG = _RL_CFG["environment"]
_REW_CFG = _RL_CFG["reward"]

PATH_NAMES = ["direct", "satellite", "mesh"]

# Energy cost per path (normalised proxy: direct < satellite < mesh)
_ENERGY_COST = np.array([0.2, 0.6, 0.9], dtype=np.float32)

# Max latency per path for normalisation
_MAX_LATENCY = np.array([100.0, 300.0, 600.0], dtype=np.float32)


class DronePathEnv(gym.Env):
    """
    Anti-jamming path selection environment.

    Each step the environment:
      1. Receives per-path RF quality scores from FL model
      2. Agent picks a path (action)
      3. Environment computes reward based on path quality under current jamming
      4. Returns next observation

    The environment can be driven by real FL scores (orchestrator) or
    internal synthetic scoring (training mode).
    """

    metadata = {"render_modes": ["human"]}

    def __init__(self, render_mode: Optional[str] = None):
        super().__init__()
        self.render_mode = render_mode
        self.max_steps    = _ENV_CFG["max_steps"]
        self.lat_window   = _ENV_CFG["latency_window"]
        self.obs_dim      = _ENV_CFG["obs_dim"]
        self.num_paths    = _ENV_CFG["num_paths"]

        # Reward weights
        self.w1 = float(_REW_CFG["w_throughput"])
        self.w2 = float(_REW_CFG["w_delay"])
        self.w3 = float(_REW_CFG["w_energy"])
        self.w4 = float(_REW_CFG["w_loss"])

        # Spaces
        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(self.obs_dim,), dtype=np.float32
        )
        self.action_space = gym.spaces.Discrete(self.num_paths)

        # Internal state
        self._path_scores:    np.ndarray = np.zeros(3, dtype=np.float32)
        self._prev_action:    int = 0
        self._lat_history:    list = [0.0] * self.lat_window
        self._prev_reward:    float = 0.0
        self._step:           int = 0
        self._jammed_paths:   np.ndarray = np.zeros(3, dtype=bool)

    # ------------------------------------------------------------------
    # Gymnasium interface
    # ------------------------------------------------------------------

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        self._path_scores  = self.np_random.uniform(0.0, 0.3, size=3).astype(np.float32)
        self._prev_action  = 0
        self._lat_history  = [0.0] * self.lat_window
        self._prev_reward  = 0.0
        self._step         = 0
        self._jammed_paths = np.zeros(3, dtype=bool)

        obs = self._build_obs()
        return obs, {}

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict]:
        assert self.action_space.contains(action), f"Invalid action {action}"

        # Simulate next RF state (in orchestrator mode, path_scores are injected externally)
        self._evolve_rf_state()

        # Compute reward for chosen path
        reward = self._compute_reward(action)

        # Update history
        lat_norm = self._get_latency_norm(action)
        self._lat_history.pop(0)
        self._lat_history.append(lat_norm)
        self._prev_action = action
        self._prev_reward = reward
        self._step += 1

        obs = self._build_obs()
        terminated = self._step >= self.max_steps
        info = {
            "path_name":   PATH_NAMES[action],
            "reward":      reward,
            "jammed_paths": self._jammed_paths.tolist(),
            "path_scores":  self._path_scores.tolist(),
            "step":        self._step,
        }
        if self.render_mode == "human":
            self._render_human(action, reward)

        return obs, reward, terminated, False, info

    def inject_threat_scores(self, path_scores: list) -> None:
        """
        External injection of FL threat scores for orchestrator-driven operation.
        Called by the orchestrator before each step().
        """
        self._path_scores = np.clip(np.array(path_scores, dtype=np.float32), 0.0, 1.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_obs(self) -> np.ndarray:
        """Assemble the 11-dimensional observation vector."""
        prev_action_onehot = np.zeros(3, dtype=np.float32)
        prev_action_onehot[self._prev_action] = 1.0

        lat_hist = np.array(self._lat_history, dtype=np.float32)
        step_norm = np.array([self._step / self.max_steps], dtype=np.float32)

        obs = np.concatenate([
            self._path_scores,       # [3]
            prev_action_onehot,      # [3]
            lat_hist,                # [3]
            [self._prev_reward],     # [1]
            step_norm,               # [1]
        ])
        return obs.astype(np.float32)

    def _evolve_rf_state(self) -> None:
        """
        Randomly evolve RF conditions (training mode).
        In orchestrator mode, path_scores are injected externally.
        Randomly jam paths with 20% probability each.
        """
        # Random jamming events
        self._jammed_paths = self.np_random.random(3) < 0.2
        for i, jammed in enumerate(self._jammed_paths):
            if jammed:
                self._path_scores[i] = float(self.np_random.uniform(0.6, 1.0))
            else:
                self._path_scores[i] = float(self.np_random.uniform(0.0, 0.3))

    def _compute_reward(self, action: int) -> float:
        """
        reward = w1*throughput_norm - w2*delay_norm - w3*energy_norm - w4*loss_norm
        All components in [0, 1].
        """
        threat = float(self._path_scores[action])

        # Throughput proxy: inversely proportional to threat
        throughput_norm = 1.0 - threat

        # Delay proxy: normalised latency
        delay_norm = self._get_latency_norm(action)

        # Energy cost: fixed per path
        energy_norm = float(_ENERGY_COST[action])

        # Packet loss proxy: proportional to threat
        loss_norm = threat * 0.8 + self.np_random.uniform(0.0, 0.1)
        loss_norm = float(np.clip(loss_norm, 0.0, 1.0))

        reward = (
            self.w1 * throughput_norm
            - self.w2 * delay_norm
            - self.w3 * energy_norm
            - self.w4 * loss_norm
        )
        return float(reward)

    def _get_latency_norm(self, action: int) -> float:
        """Normalised latency for the chosen path (0=best, 1=worst)."""
        # Synthetic latency scaled by threat level
        threat = float(self._path_scores[action])
        base_latency = _MAX_LATENCY[action] * (0.1 + 0.9 * threat)
        return float(np.clip(base_latency / _MAX_LATENCY[action], 0.0, 1.0))

    def _render_human(self, action: int, reward: float) -> None:
        print(
            f"[ENV] step={self._step:4d} | "
            f"scores=[{','.join(f'{s:.2f}' for s in self._path_scores)}] | "
            f"action={PATH_NAMES[action]:9s} | reward={reward:+.3f}"
        )


# ---------------------------------------------------------------------------
# RealDataDronePathEnv — replays preprocessed dataset rows
# ---------------------------------------------------------------------------

class RealDataDronePathEnv(DronePathEnv):
    """
    Identical to DronePathEnv but drives RF state from a real dataset CSV
    instead of random sampling.

    On each reset(), a random window of max_steps rows is chosen from the CSV.
    On each _evolve_rf_state(), the next row is consumed.
    When the window is exhausted the episode terminates (or wraps around).

    The CSV is expected to have columns:
        rssi, pdr, sinr, latency, packet_loss, jammed
    All values normalised to [0, 1] by preprocess.py.
    """

    def __init__(
        self,
        csv_path: Optional[Path] = None,
        render_mode: Optional[str] = None,
    ):
        super().__init__(render_mode=render_mode)

        # Default path from config
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
        # Keep only rows with all required columns
        required = ["rssi", "pdr", "sinr", "latency", "packet_loss", "jammed"]
        available = [c for c in required if c in self._df.columns]
        self._df = self._df[available].dropna().reset_index(drop=True)
        self._loaded = True
        import logging
        logging.getLogger(__name__).info(
            "RealDataDronePathEnv loaded %d rows from %s",
            len(self._df), self._csv_path.name,
        )

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed, options=options)
        self._load_data()

        n = len(self._df)
        # Pick a random starting row, leaving room for max_steps rows
        max_start = max(0, n - self.max_steps - 1)
        self._window_start = int(self.np_random.integers(0, max_start + 1))
        self._row_idx = self._window_start
        self._step = 0

        # Seed path scores from first real row
        self._apply_real_row()
        obs = self._build_obs()
        return obs, {}

    def _apply_real_row(self) -> None:
        """Set _path_scores and _jammed_paths from the current CSV row."""
        if self._df is None or self._row_idx >= len(self._df):
            # Wrap around
        	self._row_idx = self._window_start

        row = self._df.iloc[self._row_idx]
        jammed_val = float(row.get("jammed", 0))

        # SINR column gives the clearest signal quality indicator
        sinr_norm = float(row.get("sinr", 0.5))  # already normalised [0,1]
        # Path scores = threat probability = inverse of signal quality
        # For simplicity broadcast the row's jamming state to all 3 paths
        base_threat = jammed_val * (1.0 - sinr_norm) + (1.0 - jammed_val) * (sinr_norm * 0.1)

        # Add small per-path jitter so paths aren't identical
        noise = self.np_random.uniform(-0.05, 0.05, size=3).astype(np.float32)
        self._path_scores = np.clip(
            np.array([base_threat] * 3, dtype=np.float32) + noise, 0.0, 1.0
        )
        self._jammed_paths = self._path_scores > 0.5
        self._row_idx += 1

    def _evolve_rf_state(self) -> None:
        """Override: consume next real dataset row instead of random sampling."""
        self._apply_real_row()

    def _get_latency_norm(self, action: int) -> float:
        """Use real latency column when available."""
        if self._df is not None and self._row_idx - 1 < len(self._df):
            row = self._df.iloc[max(0, self._row_idx - 1)]
            if "latency" in row.index:
                return float(np.clip(row["latency"], 0.0, 1.0))  # already normalised
        return super()._get_latency_norm(action)
