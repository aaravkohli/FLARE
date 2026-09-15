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
  SynchronizedTraceDronePathEnv — Strict, versioned three-path CSV replay
  RealDataDronePathEnv  — Compatibility name for synchronized trace replay
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import gymnasium as gym
import numpy as np
import pandas as pd
import yaml

from rl.reward import (
    MAX_LATENCY_MS,
    PATH_NAMES as SHARED_PATH_NAMES,
    REWARD_DEFINITION,
    RewardBreakdown,
    compute_routing_reward,
)
from rl.traces import load_synchronized_trace, trace_fingerprint
from rl.reward import compute_secure_routing_reward
from schemas.contracts import ROUTING_ACTIONS_V3, ROUTING_STATE_V3

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_ENV_CFG = _RL_CFG["environment"]

PATH_NAMES = list(SHARED_PATH_NAMES)

# Max latency per path for normalisation (ms)
_MAX_LATENCY = np.array(MAX_LATENCY_MS, dtype=np.float32)


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
        self._prev_action: Optional[int] = None
        self._prev_reward: float = 0.0
        self._step: int = 0
        self._jammed_paths: np.ndarray = np.zeros(3, dtype=bool)

    def _build_obs(self) -> np.ndarray:
        """Assemble the 14-dimensional state observation."""
        prev_action_onehot = np.zeros(3, dtype=np.float32)
        if self._prev_action is not None:
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

        # An all-zero action vector explicitly represents "no previous route".
        # Encoding Direct here would disagree with the first-step reward, which
        # correctly has no switching penalty.
        self._prev_action = None
        self._prev_reward = 0.0
        self._step = 0
        self._jammed_paths = np.zeros(3, dtype=bool)

        obs = self._build_obs()
        return obs, {}

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict]:
        assert self.action_space.contains(action), f"Invalid action {action}"

        # Score the state the policy actually observed. Advancing RF conditions
        # first would assign credit using an unseen, randomly sampled state.
        reward_breakdown = self._reward_breakdown(action)
        reward = reward_breakdown.total
        decision_path_scores = self._path_scores.copy()
        decision_path_latencies = self._path_latencies.copy()
        decision_path_losses = self._path_losses.copy()
        decision_jammed_paths = self._jammed_paths.copy()

        # Update environment state trackers
        self._prev_action = action
        self._prev_reward = reward
        self._step += 1

        # Evolve RF state after scoring to produce the next observation.
        self._evolve_rf_state()
        obs = self._build_obs()
        terminated = self._step >= self.max_steps
        info = {
            "path_name": PATH_NAMES[action],
            "reward": reward,
            "reward_definition": REWARD_DEFINITION,
            "reward_components": reward_breakdown.as_dict(),
            "jammed_paths": decision_jammed_paths.tolist(),
            "path_scores": decision_path_scores.tolist(),
            "latencies": decision_path_latencies.tolist(),
            "packet_losses": decision_path_losses.tolist(),
            "next_path_scores": self._path_scores.tolist(),
            "next_latencies": self._path_latencies.tolist(),
            "next_packet_losses": self._path_losses.tolist(),
            "step": self._step,
        }

        if self.render_mode == "human":
            self._render_human(action, reward, decision_path_scores)

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
        """Compatibility wrapper returning the shared reward total."""
        return self._reward_breakdown(action).total

    def _reward_breakdown(self, action: int) -> RewardBreakdown:
        return compute_routing_reward(
            action,
            self._path_scores,
            self._path_latencies,
            self._path_losses,
            previous_action=self._prev_action,
        )


class TrustAwareDronePathEnv(DronePathEnv):
    """routing_state_v3: QoS state plus cross-layer security evidence.

    Additional inputs are client trust, insider risk, containment severity, and
    evidence freshness.  Action 3 is an executable fail-closed HOLD.
    """

    contract_name = ROUTING_STATE_V3

    def __init__(self, render_mode: Optional[str] = None):
        super().__init__(render_mode=render_mode)
        self.obs_dim = 18
        self.num_paths = 3
        self.action_space = gym.spaces.Discrete(len(ROUTING_ACTIONS_V3))
        self.observation_space = gym.spaces.Box(
            low=np.array([0.0] * 12 + [-np.inf, 0.0] + [0.0] * 4, dtype=np.float32),
            high=np.array([1.0] * 12 + [np.inf, 1.0] + [1.0] * 4, dtype=np.float32),
            dtype=np.float32,
        )
        self._client_trust = 1.0
        self._insider_risk = 0.0
        self._containment_score = 0.0
        self._evidence_freshness = 1.0

    def inject_security_context(
        self,
        *,
        client_trust: float,
        insider_risk: float,
        containment_score: float,
        evidence_freshness: float,
    ) -> None:
        self._client_trust = float(np.clip(client_trust, 0.0, 1.0))
        self._insider_risk = float(np.clip(insider_risk, 0.0, 1.0))
        self._containment_score = float(np.clip(containment_score, 0.0, 1.0))
        self._evidence_freshness = float(np.clip(evidence_freshness, 0.0, 1.0))

    def _build_obs(self) -> np.ndarray:
        prev_action_onehot = np.zeros(3, dtype=np.float32)
        if self._prev_action is not None and self._prev_action < 3:
            prev_action_onehot[self._prev_action] = 1.0
        base = np.concatenate([
            self._path_scores,
            self._path_latencies,
            self._path_losses,
            prev_action_onehot,
            [self._prev_reward],
            [self._step / self.max_steps],
        ])
        security = np.asarray([
            self._client_trust,
            self._insider_risk,
            self._containment_score,
            self._evidence_freshness,
        ], dtype=np.float32)
        return np.concatenate([base, security]).astype(np.float32)

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        # Base reset safely builds through this class after security defaults are
        # established in __init__.
        observation, info = super().reset(seed=seed, options=options)
        self.inject_security_context(
            client_trust=float(self.np_random.uniform(0.75, 1.0)),
            insider_risk=float(self.np_random.uniform(0.0, 0.20)),
            containment_score=0.0,
            evidence_freshness=float(self.np_random.uniform(0.8, 1.0)),
        )
        return self._build_obs(), info

    def step(self, action: int):
        assert self.action_space.contains(action), f"Invalid action {action}"
        reward_breakdown = self._reward_breakdown(action)
        decision_scores = self._path_scores.copy()
        decision_latencies = self._path_latencies.copy()
        decision_losses = self._path_losses.copy()
        self._prev_action = int(action)
        self._prev_reward = reward_breakdown.total
        self._step += 1
        self._evolve_rf_state()
        # Security incidents are uncommon but sustained for learnable episodes.
        if self.np_random.random() < 0.04:
            self._insider_risk = float(self.np_random.uniform(0.75, 1.0))
            self._client_trust = float(self.np_random.uniform(0.0, 0.35))
        else:
            self._insider_risk = max(0.0, self._insider_risk * 0.96)
            self._client_trust = min(1.0, self._client_trust + 0.01)
        self._containment_score = max(self._insider_risk, 1.0 - self._client_trust)
        terminated = self._step >= self.max_steps
        return self._build_obs(), reward_breakdown.total, terminated, False, {
            "path_name": ROUTING_ACTIONS_V3[int(action)],
            "reward": reward_breakdown.total,
            "reward_definition": "routing_security_qos_v3",
            "reward_components": reward_breakdown.as_dict(),
            "path_scores": decision_scores.tolist(),
            "latencies": decision_latencies.tolist(),
            "packet_losses": decision_losses.tolist(),
            "security_context": self._build_obs()[-4:].tolist(),
            "step": self._step,
        }

    def _reward_breakdown(self, action: int) -> RewardBreakdown:
        # Unsafe forwarding from a likely compromised node gets an additional
        # cost so the learnable optimum becomes HOLD under corroborated risk.
        result = compute_secure_routing_reward(
            action,
            self._path_scores,
            self._path_latencies,
            self._path_losses,
            previous_action=self._prev_action,
            hold_penalty=float(_RL_CFG.get("routing_v3", {}).get("hold_penalty", 0.35)),
        )
        if action < 3 and max(self._insider_risk, 1.0 - self._client_trust) >= 0.72:
            payload = result.as_dict()
            payload["loss_penalty"] += 1.0
            payload["total"] -= 1.0
            payload.pop("held", None)
            return RewardBreakdown(**payload)
        return result

    def _render_human(
        self,
        action: int,
        reward: float,
        path_scores: Optional[np.ndarray] = None,
    ) -> None:
        displayed_scores = self._path_scores if path_scores is None else path_scores
        print(
            f"[ENV] step={self._step:4d} | "
            f"threats=[{','.join(f'{s:.2f}' for s in displayed_scores)}] | "
            f"action={PATH_NAMES[action]:9s} | reward={reward:+.3f}"
        )


# ---------------------------------------------------------------------------
# Synchronized Three-Path Trace Environment (Replay Mode)
# ---------------------------------------------------------------------------

class SynchronizedTraceDronePathEnv(DronePathEnv):
    """
    Replays complete direct/satellite/mesh state from a versioned trace CSV.

    Unlike the historical single-row adapter, this environment never invents
    route variants.  Every observation field comes from the same recorded or
    generated timestep, and actions do not affect subsequent trace rows.
    """

    def __init__(
        self,
        csv_path: Optional[Path] = None,
        render_mode: Optional[str] = None,
        *,
        cycle_episodes: bool = False,
        scenario: Optional[str] = None,
    ):
        super().__init__(render_mode=render_mode)
        if csv_path is None:
            configured = _RL_CFG.get("paths", {}).get(
                "synchronized_trace_csv",
                "datasets/processed/rl_trace_iid_train.csv",
            )
            csv_path = _BASE / configured
        self._csv_path = Path(csv_path)
        self._df: Optional[pd.DataFrame] = None
        self._episode_rows: Optional[pd.DataFrame] = None
        self._episode_ids: tuple[str, ...] = ()
        self._episode_id: Optional[str] = None
        self._episode_length: int = 0
        self._trace_fingerprint: Optional[str] = None
        self._cycle_episodes = bool(cycle_episodes)
        self._scenario_filter = scenario
        self._next_episode_index = 0
        self._loaded = False

    def _load_data(self) -> None:
        if self._loaded:
            return
        if not self._csv_path.exists():
            raise FileNotFoundError(
                f"Synchronized RL trace not found: {self._csv_path}\n"
                "Run: python -m rl.traces --scenario iid"
            )
        self._df = load_synchronized_trace(self._csv_path)
        if self._scenario_filter is not None:
            self._df = self._df[
                self._df["scenario"] == self._scenario_filter
            ].reset_index(drop=True)
            if self._df.empty:
                raise ValueError(
                    f"trace {self._csv_path} does not contain scenario "
                    f"{self._scenario_filter!r}"
                )
        self._episode_ids = tuple(self._df["episode_id"].drop_duplicates())
        self._trace_fingerprint = trace_fingerprint(self._df)
        self._loaded = True
        logger.info(
            "SynchronizedTraceDronePathEnv loaded %d rows across %d episodes from %s",
            len(self._df),
            len(self._episode_ids),
            self._csv_path.name,
        )

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[dict] = None
    ) -> Tuple[np.ndarray, dict]:
        self._load_data()
        super().reset(seed=seed, options=options)

        requested_episode = (options or {}).get("episode_id")
        if requested_episode is not None:
            requested_episode = str(requested_episode)
            if requested_episode not in self._episode_ids:
                raise ValueError(f"unknown trace episode_id {requested_episode!r}")
            self._episode_id = requested_episode
        else:
            # Consecutive explicit seeds traverse each episode once before
            # repeating, which makes paired evaluation complete and auditable.
            # Unseeded training resets continue to sample from Gym's RNG.
            if seed is not None:
                selected_index = int(seed) % len(self._episode_ids)
                self._next_episode_index = (
                    selected_index + 1
                ) % len(self._episode_ids)
            elif self._cycle_episodes:
                selected_index = self._next_episode_index
                self._next_episode_index = (
                    self._next_episode_index + 1
                ) % len(self._episode_ids)
            else:
                selected_index = int(self.np_random.integers(0, len(self._episode_ids)))
            self._episode_id = self._episode_ids[selected_index]

        assert self._df is not None
        self._episode_rows = self._df[
            self._df["episode_id"] == self._episode_id
        ].reset_index(drop=True)
        self._episode_length = min(len(self._episode_rows), self.max_steps)
        self._step = 0
        self._apply_trace_step(0)
        obs = self._build_obs()
        first_row = self._episode_rows.iloc[0]
        return obs, {
            "episode_id": self._episode_id,
            "scenario": str(first_row["scenario"]),
            "trace_source": str(first_row["source"]),
            "trace_generation_seed": int(first_row["generation_seed"]),
            "trace_fingerprint": self._trace_fingerprint,
            "episode_steps": self._episode_length,
        }

    def _apply_trace_step(self, trace_step: int) -> None:
        """Load one synchronized row without interpolation or random variation."""
        if self._episode_rows is None or trace_step >= self._episode_length:
            return
        row = self._episode_rows.iloc[trace_step]
        self._path_scores = np.asarray(
            [row[f"{path_name}_threat_score"] for path_name in PATH_NAMES],
            dtype=np.float32,
        )
        self._path_latencies = np.asarray(
            [row[f"{path_name}_latency_norm"] for path_name in PATH_NAMES],
            dtype=np.float32,
        )
        self._path_losses = np.asarray(
            [row[f"{path_name}_packet_loss"] for path_name in PATH_NAMES],
            dtype=np.float32,
        )
        self._jammed_paths = np.asarray(
            [bool(row[f"{path_name}_jammed"]) for path_name in PATH_NAMES],
            dtype=bool,
        )

    def _evolve_rf_state(self) -> None:
        """Advance to the next synchronized row after scoring the current one."""
        self._apply_trace_step(self._step)

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict]:
        observation, reward, terminated, truncated, info = super().step(action)
        terminated = terminated or self._step >= self._episode_length
        assert self._episode_rows is not None
        decision_row = self._episode_rows.iloc[self._step - 1]
        info.update({
            "episode_id": self._episode_id,
            "scenario": str(decision_row["scenario"]),
            "trace_source": str(decision_row["source"]),
            "trace_generation_seed": int(decision_row["generation_seed"]),
            "trace_fingerprint": self._trace_fingerprint,
            "trace_step": int(decision_row["step"]),
        })
        return observation, reward, terminated, truncated, info


class RealDataDronePathEnv(SynchronizedTraceDronePathEnv):
    """Backward-compatible name for strict synchronized trace replay.

    The name is retained for imports only.  The environment no longer accepts
    the single-link FL ``test.csv`` or synthesizes route variants from it.
    """


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
