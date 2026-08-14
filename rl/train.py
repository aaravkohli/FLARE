"""
rl/train.py — Upgraded Training Pipeline  [FLARE v2]

Supports SOTA training loops for both:
  1. Dueling Double DQN (using Stable-Baselines3)
  2. Custom Discrete SAC (using PyTorch training loop)

Includes TensorBoard telemetry and periodic checkpointing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import yaml

from rl.env import DronePathEnv, RealDataDronePathEnv

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_TRAIN_CFG = _RL_CFG["training"]
_ENV_CFG = _RL_CFG["environment"]
_PATHS_CFG = _RL_CFG["paths"]

os.makedirs(_BASE / "models", exist_ok=True)
os.makedirs(_BASE / _PATHS_CFG["log_dir"], exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RL-TRAIN] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Training: Dueling Double DQN (Stable-Baselines3)
# ---------------------------------------------------------------------------

def train_dqn(total_timesteps: int, use_real_data: bool, tb_log: Optional[str]) -> None:
    from stable_baselines3 import DQN
    from stable_baselines3.common.callbacks import CheckpointCallback, EvalCallback
    from stable_baselines3.common.monitor import Monitor

    EnvClass = RealDataDronePathEnv if use_real_data else DronePathEnv
    logger.info("[DQN] Initialising environment: %s", EnvClass.__name__)
    train_env = Monitor(EnvClass())
    eval_env = Monitor(EnvClass())

    policy_kwargs = dict(
        net_arch=[128, 128],
    )

    model = DQN(
        policy="MlpPolicy",
        env=train_env,
        learning_rate=_TRAIN_CFG["learning_rate"],
        buffer_size=_TRAIN_CFG["buffer_size"],
        batch_size=_TRAIN_CFG["batch_size"],
        exploration_fraction=_TRAIN_CFG.get("exploration_fraction", 0.2),
        exploration_final_eps=_TRAIN_CFG.get("exploration_final_eps", 0.05),
        train_freq=_TRAIN_CFG.get("train_freq", 4),
        target_update_interval=_TRAIN_CFG.get("target_update_interval", 1000),
        tensorboard_log=tb_log,
        policy_kwargs=policy_kwargs,
        verbose=1,
    )

    checkpoint_cb = CheckpointCallback(
        save_freq=10_000,
        save_path=str(_BASE / "models"),
        name_prefix="rl_checkpoint",
    )

    eval_cb = EvalCallback(
        eval_env,
        best_model_save_path=str(_BASE / "models"),
        log_path=str(_BASE / _PATHS_CFG["log_dir"]),
        eval_freq=5_000,
        n_eval_episodes=10,
        deterministic=True,
        verbose=1,
    )

    logger.info("[DQN] Starting training for %d steps.", total_timesteps)
    model.learn(
        total_timesteps=total_timesteps,
        callback=[checkpoint_cb, eval_cb],
    )

    save_path = str(_BASE / _PATHS_CFG["model_save"])
    model.save(save_path)
    logger.info("[DQN] Training complete. Saved model → %s", save_path)


# ---------------------------------------------------------------------------
# Training: Discrete Soft Actor-Critic (PyTorch)
# ---------------------------------------------------------------------------

def train_sac(total_timesteps: int, use_real_data: bool, tb_log: Optional[str]) -> None:
    from rl.sac_discrete import DiscreteSACAgent, ReplayBuffer
    from torch.utils.tensorboard import SummaryWriter

    EnvClass = RealDataDronePathEnv if use_real_data else DronePathEnv
    logger.info("[SAC] Initialising environment: %s", EnvClass.__name__)
    env = EnvClass()
    eval_env = EnvClass()

    sac_cfg = _TRAIN_CFG.get("sac", {})
    agent = DiscreteSACAgent(
        state_dim=_ENV_CFG["obs_dim"],
        action_dim=_ENV_CFG["num_paths"],
        policy_lr=sac_cfg.get("policy_lr", 0.0003),
        critic_lr=sac_cfg.get("critic_lr", 0.0003),
        alpha_lr=sac_cfg.get("alpha_lr", 0.0003),
        initial_alpha=sac_cfg.get("initial_alpha", 0.2),
        target_entropy_ratio=sac_cfg.get("target_entropy_ratio", 0.98),
        tau=sac_cfg.get("tau", 0.005),
    )

    replay_buffer = ReplayBuffer(max_size=_TRAIN_CFG["buffer_size"])
    writer = SummaryWriter(tb_log) if tb_log else None

    # Training state
    state, _ = env.reset()
    episode_reward = 0.0
    episode_steps = 0
    best_eval_reward = -float("inf")

    batch_size = _TRAIN_CFG["batch_size"]
    start_time = time.time()

    logger.info("[SAC] Starting Discrete SAC training for %d steps.", total_timesteps)

    for step in range(1, total_timesteps + 1):
        # Action selection (explore with stochastic policy during training)
        action, _ = agent.predict(state, deterministic=False)

        next_state, reward, terminated, truncated, _ = env.step(action)
        done = terminated or truncated

        # Push transition to buffer
        replay_buffer.push(state, action, reward, next_state, done)

        state = next_state
        episode_reward += reward
        episode_steps += 1

        if done:
            state, _ = env.reset()
            if writer:
                writer.add_scalar("rollout/ep_rew_mean", episode_reward, step)
                writer.add_scalar("rollout/ep_len_mean", episode_steps, step)

            logger.info(
                "[SAC] Step %d/%d | Ep Reward = %.2f | Ep Steps = %d",
                step, total_timesteps, episode_reward, episode_steps,
            )
            episode_reward = 0.0
            episode_steps = 0

        # Update parameters
        if len(replay_buffer) > batch_size * 2:
            metrics = agent.update_parameters(replay_buffer, batch_size)

            # Log metrics periodically
            if step % 100 == 0 and writer:
                writer.add_scalar("train/critic_loss", metrics["critic_loss"], step)
                writer.add_scalar("train/actor_loss", metrics["actor_loss"], step)
                writer.add_scalar("train/entropy", metrics["entropy"], step)
                writer.add_scalar("train/alpha", metrics["alpha"], step)

        # Evaluation Callback (every 5,000 steps)
        if step % 5000 == 0:
            eval_rewards = []
            for _ in range(5):  # 5 eval episodes
                s_eval, _ = eval_env.reset()
                d_eval = False
                r_eval_total = 0.0
                while not d_eval:
                    a_eval, _ = agent.predict(s_eval, deterministic=True)
                    s_eval, r_eval, term, trunc, _ = eval_env.step(a_eval)
                    d_eval = term or trunc
                    r_eval_total += r_eval
                eval_rewards.append(r_eval_total)

            mean_eval = float(np.mean(eval_rewards))
            logger.info("[SAC Eval] Step %d | Mean Eval Reward = %.2f", step, mean_eval)
            if writer:
                writer.add_scalar("eval/mean_reward", mean_eval, step)

            # Save best model
            if mean_eval > best_eval_reward:
                best_eval_reward = mean_eval
                best_path = str(_BASE / "models" / "best_sac_model.pth")
                agent.save(best_path)
                logger.info("[SAC Eval] New best model saved (mean_reward=%.2f) → %s", mean_eval, best_path)

    # Save final model
    save_path = str(_BASE / _PATHS_CFG.get("sac_model_save", "models/rl_sac_model.pth"))
    agent.save(save_path)
    logger.info("[SAC] Training complete. Saved model → %s", save_path)
    if writer:
        writer.close()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RL Training Pipeline (FLARE v2)")
    parser.add_argument("--timesteps", type=int, default=_TRAIN_CFG["total_timesteps"])
    parser.add_argument("--real-data", action="store_true", help="Use preprocessed test.csv")
    parser.add_argument("--algo", type=str, default=None, choices=["dqn", "sac"],
                        help="Override default training algorithm")
    args = parser.parse_args()

    algo = args.algo if args.algo else _TRAIN_CFG.get("algo", "dqn").lower()

    # Configure TensorBoard
    tb_log = None
    try:
        import tensorboard
        tb_log = str(_BASE / _PATHS_CFG["log_dir"])
    except ImportError:
        logger.warning("TensorBoard not installed — skipping telemetry logging. Run: pip install tensorboard")

    if algo == "sac":
        train_sac(total_timesteps=args.timesteps, use_real_data=args.real_data, tb_log=tb_log)
    else:
        train_dqn(total_timesteps=args.timesteps, use_real_data=args.real_data, tb_log=tb_log)


if __name__ == "__main__":
    main()
