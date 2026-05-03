"""
rl/train.py — [REAL]
DQN training script using Stable-Baselines3.

Trains a Double DQN policy against DronePathEnv and saves the model
to models/rl_model.zip.

Usage:
  python rl/train.py [--timesteps 50000]
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when running as `python rl/train.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from stable_baselines3 import DQN
from stable_baselines3.common.callbacks import CheckpointCallback, EvalCallback
from stable_baselines3.common.monitor import Monitor

from rl.env import DronePathEnv

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_TRAIN_CFG = _RL_CFG["training"]
_PATHS_CFG = _RL_CFG["paths"]

os.makedirs(_BASE / "models", exist_ok=True)
os.makedirs(_BASE / _PATHS_CFG["log_dir"], exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RL-TRAIN] %(message)s")
logger = logging.getLogger(__name__)


def train(total_timesteps: int = _TRAIN_CFG["total_timesteps"]):
    # Training environment
    train_env = Monitor(DronePathEnv())

    # Evaluation environment (separate instance)
    eval_env = Monitor(DronePathEnv())

    # Only enable TensorBoard logging if the package is installed
    try:
        import tensorboard  # noqa: F401
        tb_log = str(_BASE / _PATHS_CFG["log_dir"])
    except ImportError:
        logger.warning("tensorboard not installed — skipping TB logging. Run: pip install tensorboard")
        tb_log = None

    model = DQN(
        policy="MlpPolicy",
        env=train_env,
        learning_rate=_TRAIN_CFG["learning_rate"],
        buffer_size=_TRAIN_CFG["buffer_size"],
        batch_size=_TRAIN_CFG["batch_size"],
        exploration_fraction=_TRAIN_CFG["exploration_fraction"],
        exploration_final_eps=_TRAIN_CFG["exploration_final_eps"],
        train_freq=_TRAIN_CFG["train_freq"],
        target_update_interval=_TRAIN_CFG["target_update_interval"],
        tensorboard_log=tb_log,
        verbose=1,
    )

    # Save checkpoints every 10 000 steps
    checkpoint_cb = CheckpointCallback(
        save_freq=10_000,
        save_path=str(_BASE / "models"),
        name_prefix="rl_checkpoint",
    )

    # Evaluate every 5 000 steps; save best model
    eval_cb = EvalCallback(
        eval_env,
        best_model_save_path=str(_BASE / "models"),
        log_path=str(_BASE / _PATHS_CFG["log_dir"]),
        eval_freq=5_000,
        n_eval_episodes=10,
        deterministic=True,
        verbose=1,
    )

    logger.info("Starting DQN training for %d timesteps.", total_timesteps)
    model.learn(
        total_timesteps=total_timesteps,
        callback=[checkpoint_cb, eval_cb],
        progress_bar=False,   # set True if tqdm is installed
    )

    save_path = str(_BASE / _PATHS_CFG["model_save"])
    model.save(save_path)
    logger.info("RL model saved → %s", save_path)


def main():
    parser = argparse.ArgumentParser(description="RL DQN Training")
    parser.add_argument("--timesteps", type=int, default=_TRAIN_CFG["total_timesteps"])
    args = parser.parse_args()
    train(total_timesteps=args.timesteps)


if __name__ == "__main__":
    main()
