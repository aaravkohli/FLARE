"""
rl/train.py — Upgraded Training Pipeline  [FLARE v2]

Supports training loops for both:
  1. Standard DQN (using Stable-Baselines3)
  2. Custom Discrete SAC (using PyTorch training loop)

Includes TensorBoard telemetry and periodic checkpointing.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import yaml

from rl.checkpoint import checkpoint_metadata_path, write_checkpoint_metadata
from rl.env import DronePathEnv, SynchronizedTraceDronePathEnv
from rl.traces import validate_disjoint_trace_files

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_TRAIN_CFG = _RL_CFG["training"]
_ENV_CFG = _RL_CFG["environment"]
_PATHS_CFG = _RL_CFG["paths"]

os.makedirs(_BASE / "models", exist_ok=True)
os.makedirs(_BASE / _PATHS_CFG["log_dir"], exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RL-TRAIN] %(message)s")
logger = logging.getLogger(__name__)


def _write_checkpoint_metadata(
    checkpoint_path: str | Path,
    *,
    algorithm: str,
    total_timesteps: int,
    use_trace_data: bool,
    training_seed: int,
    training_provenance: Optional[dict] = None,
) -> Path:
    sidecar = write_checkpoint_metadata(
        checkpoint_path,
        algorithm=algorithm,
        observation_dim=_ENV_CFG["obs_dim"],
        action_count=_ENV_CFG["num_paths"],
        max_episode_steps=_ENV_CFG["max_steps"],
        training_timesteps=total_timesteps,
        training_mode="synchronized_trace" if use_trace_data else "synthetic",
        training_seed=training_seed,
        training_provenance=training_provenance,
    )
    logger.info("[%s] Saved checkpoint metadata → %s", algorithm.upper(), sidecar)


def _copy_checkpoint_with_metadata(source: Path, destination: Path) -> None:
    """Promote a validated training artifact without separating its sidecar."""
    source_metadata = checkpoint_metadata_path(source)
    if not source.is_file() or not source_metadata.is_file():
        raise FileNotFoundError(
            f"checkpoint promotion requires {source} and {source_metadata}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    shutil.copy2(source_metadata, checkpoint_metadata_path(destination))


# ---------------------------------------------------------------------------
# Training: Standard DQN (Stable-Baselines3)
# ---------------------------------------------------------------------------

def train_dqn(
    total_timesteps: int,
    use_trace_data: bool,
    tb_log: Optional[str],
    *,
    seed: int,
    output_path: Optional[Path] = None,
    trace_csv: Optional[Path] = None,
    trace_eval_csv: Optional[Path] = None,
    evaluation_episodes: Optional[int] = None,
    evaluation_frequency: int = 5_000,
    evaluation_seed: Optional[int] = None,
) -> Path:
    from stable_baselines3 import DQN
    from stable_baselines3.common.callbacks import CheckpointCallback, EvalCallback
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.utils import set_random_seed

    set_random_seed(seed)
    training_provenance: Optional[dict] = None
    validation_episode_count = int(
        evaluation_episodes
        if evaluation_episodes is not None
        else _TRAIN_CFG.get("evaluation_episodes", 50)
    )
    if validation_episode_count < 1:
        raise ValueError("evaluation_episodes must be at least 1")
    if evaluation_frequency < 1:
        raise ValueError("evaluation_frequency must be at least 1")
    validation_seed = int(
        evaluation_seed
        if evaluation_seed is not None
        else _TRAIN_CFG.get("evaluation_seed", 42_000)
    )

    class MetadataCheckpointCallback(CheckpointCallback):
        """Add a compatibility sidecar after each periodic model save."""

        def _on_step(self) -> bool:
            result = super()._on_step()
            if self.n_calls % self.save_freq == 0:
                _write_checkpoint_metadata(
                    self._checkpoint_path(extension="zip"),
                    algorithm="dqn",
                    total_timesteps=self.num_timesteps,
                    use_trace_data=use_trace_data,
                    training_seed=seed,
                    training_provenance=training_provenance,
                )
            return result

    class MetadataEvalCallback(EvalCallback):
        """Add a compatibility sidecar whenever EvalCallback saves a new best."""

        def _on_step(self) -> bool:
            previous_best = self.best_mean_reward
            if self.eval_freq > 0 and self.n_calls % self.eval_freq == 0:
                # Rewind the validation environment so every checkpoint is
                # scored on the same ordered trace episodes.
                self.eval_env.seed(validation_seed)
            result = super()._on_step()
            if self.best_mean_reward > previous_best:
                _write_checkpoint_metadata(
                    Path(self.best_model_save_path) / "best_model.zip",
                    algorithm="dqn",
                    total_timesteps=self.num_timesteps,
                    use_trace_data=use_trace_data,
                    training_seed=seed,
                    training_provenance=training_provenance,
                )
            return result

    if use_trace_data:
        configured_train_trace = _BASE / _PATHS_CFG["synchronized_trace_csv"]
        configured_eval_trace = _BASE / _PATHS_CFG["synchronized_trace_eval_csv"]
        training_trace = trace_csv or configured_train_trace
        evaluation_trace = trace_eval_csv or configured_eval_trace
        training_provenance = validate_disjoint_trace_files(
            training_trace,
            evaluation_trace,
        )
        training_environment_factory = lambda: SynchronizedTraceDronePathEnv(
            training_trace
        )
        evaluation_environment_factory = lambda: SynchronizedTraceDronePathEnv(
            evaluation_trace,
            cycle_episodes=True,
        )
        environment_name = SynchronizedTraceDronePathEnv.__name__
    else:
        training_environment_factory = DronePathEnv
        evaluation_environment_factory = DronePathEnv
        environment_name = DronePathEnv.__name__
    logger.info("[DQN] Initialising environment: %s", environment_name)
    train_env = Monitor(training_environment_factory())
    eval_env = Monitor(evaluation_environment_factory())
    train_env.reset(seed=seed)
    eval_env.reset(seed=validation_seed)

    resolved_output_path: Optional[Path] = None
    if output_path is not None:
        resolved_output_path = output_path
        if not resolved_output_path.is_absolute():
            resolved_output_path = _BASE / resolved_output_path
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        run_name = resolved_output_path.stem
        checkpoint_directory = resolved_output_path.parent / f"{run_name}_checkpoints"
        best_model_directory = resolved_output_path.parent / f"{run_name}_evaluation"
        evaluation_log_directory = best_model_directory
    else:
        checkpoint_directory = _BASE / "models"
        best_model_directory = _BASE / "models"
        evaluation_log_directory = _BASE / _PATHS_CFG["log_dir"]

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
        seed=seed,
        verbose=int(_TRAIN_CFG.get("verbose", 1)),
    )

    checkpoint_cb = MetadataCheckpointCallback(
        save_freq=10_000,
        save_path=str(checkpoint_directory),
        name_prefix="rl_checkpoint",
    )

    eval_cb = MetadataEvalCallback(
        eval_env,
        best_model_save_path=str(best_model_directory),
        log_path=str(evaluation_log_directory),
        eval_freq=evaluation_frequency,
        n_eval_episodes=validation_episode_count,
        deterministic=True,
        verbose=1,
    )

    logger.info(
        "[DQN] Starting training for %d steps with seed %d.",
        total_timesteps,
        seed,
    )
    model.learn(
        total_timesteps=total_timesteps,
        callback=[checkpoint_cb, eval_cb],
    )

    if resolved_output_path is not None:
        final_path = resolved_output_path
    else:
        final_path = _BASE / _PATHS_CFG.get(
            "final_model_save",
            "models/rl_final_model.zip",
        )
    if not final_path.is_absolute():
        final_path = _BASE / final_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(final_path))
    _write_checkpoint_metadata(
        final_path,
        algorithm="dqn",
        total_timesteps=total_timesteps,
        use_trace_data=use_trace_data,
        training_seed=seed,
        training_provenance=training_provenance,
    )
    logger.info("[DQN] Training complete. Saved final model → %s", final_path)

    if eval_cb.best_mean_reward > -float("inf"):
        best_path = best_model_directory / "best_model.zip"
        if resolved_output_path is None:
            promoted_path = best_path
            promotion_label = "candidate"
        else:
            promoted_path = resolved_output_path.with_name(
                f"{resolved_output_path.stem}_best.zip"
            )
            promotion_label = "run-specific"
        if promoted_path != best_path:
            _copy_checkpoint_with_metadata(best_path, promoted_path)
        logger.info(
            "[DQN] Preserved validation-best checkpoint (reward=%.4f) "
            "as %s artifact → %s",
            eval_cb.best_mean_reward,
            promotion_label,
            promoted_path,
        )
        train_env.close()
        eval_env.close()
        return promoted_path
    train_env.close()
    eval_env.close()
    return final_path


# ---------------------------------------------------------------------------
# Training: Discrete Soft Actor-Critic (PyTorch)
# ---------------------------------------------------------------------------

def train_sac(
    total_timesteps: int,
    use_trace_data: bool,
    tb_log: Optional[str],
    *,
    seed: int,
    output_path: Optional[Path] = None,
    trace_csv: Optional[Path] = None,
    trace_eval_csv: Optional[Path] = None,
) -> None:
    from rl.sac_discrete import DiscreteSACAgent, ReplayBuffer
    from stable_baselines3.common.utils import set_random_seed
    from torch.utils.tensorboard import SummaryWriter

    set_random_seed(seed)
    training_provenance: Optional[dict] = None

    if use_trace_data:
        configured_train_trace = _BASE / _PATHS_CFG["synchronized_trace_csv"]
        configured_eval_trace = _BASE / _PATHS_CFG["synchronized_trace_eval_csv"]
        training_trace = trace_csv or configured_train_trace
        evaluation_trace = trace_eval_csv or configured_eval_trace
        training_provenance = validate_disjoint_trace_files(
            training_trace,
            evaluation_trace,
        )
        training_environment_factory = lambda: SynchronizedTraceDronePathEnv(
            training_trace
        )
        evaluation_environment_factory = lambda: SynchronizedTraceDronePathEnv(
            evaluation_trace,
            cycle_episodes=True,
        )
        environment_name = SynchronizedTraceDronePathEnv.__name__
    else:
        training_environment_factory = DronePathEnv
        evaluation_environment_factory = DronePathEnv
        environment_name = DronePathEnv.__name__
    logger.info("[SAC] Initialising environment: %s", environment_name)
    env = training_environment_factory()
    eval_env = evaluation_environment_factory()

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
    state, _ = env.reset(seed=seed)
    episode_reward = 0.0
    episode_steps = 0
    best_eval_reward = -float("inf")

    batch_size = _TRAIN_CFG["batch_size"]
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
            for eval_index in range(5):
                s_eval, _ = eval_env.reset(seed=seed + 10_000 + eval_index)
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
                _write_checkpoint_metadata(
                    best_path,
                    algorithm="sac",
                    total_timesteps=step,
                    use_trace_data=use_trace_data,
                    training_seed=seed,
                    training_provenance=training_provenance,
                )
                logger.info("[SAC Eval] New best model saved (mean_reward=%.2f) → %s", mean_eval, best_path)

    # Save final model
    save_path = output_path or (
        _BASE / _PATHS_CFG.get("sac_model_save", "models/rl_sac_model.pth")
    )
    if not save_path.is_absolute():
        save_path = _BASE / save_path
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path = str(save_path)
    agent.save(save_path)
    _write_checkpoint_metadata(
        save_path,
        algorithm="sac",
        total_timesteps=total_timesteps,
        use_trace_data=use_trace_data,
        training_seed=seed,
        training_provenance=training_provenance,
    )
    logger.info("[SAC] Training complete. Saved model → %s", save_path)
    if writer:
        writer.close()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RL Training Pipeline (FLARE v2)")
    parser.add_argument("--timesteps", type=int, default=_TRAIN_CFG["total_timesteps"])
    parser.add_argument(
        "--trace-data",
        action="store_true",
        help="Train on a strict synchronized three-path trace CSV",
    )
    parser.add_argument(
        "--trace-csv",
        type=Path,
        default=None,
        help="Trace CSV override; implies --trace-data",
    )
    parser.add_argument(
        "--trace-eval-csv",
        type=Path,
        default=None,
        help="Disjoint validation trace CSV override; implies --trace-data",
    )
    parser.add_argument("--algo", type=str, default=None, choices=["dqn", "sac"],
                        help="Override default training algorithm")
    parser.add_argument(
        "--seed",
        type=int,
        default=int(_TRAIN_CFG.get("seed", 42)),
        help="Training/environment seed (default: config training.seed)",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Override the final checkpoint path (useful for smoke tests)",
    )
    parser.add_argument(
        "--no-tensorboard",
        action="store_true",
        help="Disable TensorBoard event output",
    )
    parser.add_argument(
        "--evaluation-episodes",
        type=int,
        default=None,
        help="Override DQN validation episodes per evaluation checkpoint",
    )
    parser.add_argument(
        "--evaluation-frequency",
        type=int,
        default=5_000,
        help="DQN validation/checkpoint-selection interval in environment steps",
    )
    parser.add_argument(
        "--evaluation-seed",
        type=int,
        default=None,
        help="Fixed DQN validation environment seed (default: config value)",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Atomically publish the validated DQN candidate via deployment_manifest.json",
    )
    parser.add_argument(
        "--provenance-run-id",
        help="Immutable training/evaluation run ID required with --deploy",
    )
    args = parser.parse_args()

    if args.timesteps < 1:
        parser.error("--timesteps must be at least 1")
    if args.evaluation_episodes is not None and args.evaluation_episodes < 1:
        parser.error("--evaluation-episodes must be at least 1")
    if args.evaluation_frequency < 1:
        parser.error("--evaluation-frequency must be at least 1")
    if args.deploy and not args.provenance_run_id:
        parser.error("--deploy requires --provenance-run-id")

    use_trace_data = (
        args.trace_data
        or args.trace_csv is not None
        or args.trace_eval_csv is not None
    )

    algo = args.algo if args.algo else _TRAIN_CFG.get("algo", "dqn").lower()

    # Configure TensorBoard
    tb_log = None
    if not args.no_tensorboard:
        try:
            import tensorboard
            tb_log = str(_BASE / _PATHS_CFG["log_dir"])
        except ImportError:
            logger.warning("TensorBoard not installed — skipping telemetry logging. Run: pip install tensorboard")

    if algo == "sac":
        train_sac(
            total_timesteps=args.timesteps,
            use_trace_data=use_trace_data,
            tb_log=tb_log,
            seed=args.seed,
            output_path=args.output_path,
            trace_csv=args.trace_csv,
            trace_eval_csv=args.trace_eval_csv,
        )
    else:
        candidate_path = train_dqn(
            total_timesteps=args.timesteps,
            use_trace_data=use_trace_data,
            tb_log=tb_log,
            seed=args.seed,
            output_path=args.output_path,
            trace_csv=args.trace_csv,
            trace_eval_csv=args.trace_eval_csv,
            evaluation_episodes=args.evaluation_episodes,
            evaluation_frequency=args.evaluation_frequency,
            evaluation_seed=args.evaluation_seed,
        )
        if args.deploy:
            from runtime.model_deployment import publish_checkpoint

            published = publish_checkpoint(
                base=_BASE,
                manifest_path=_BASE / "models" / "deployment_manifest.json",
                model_name="routing",
                checkpoint_path=candidate_path,
                metadata_path=checkpoint_metadata_path(candidate_path),
                contract="routing_state_v2",
                provenance_run_id=args.provenance_run_id,
            )
            logger.info(
                "Published atomic routing deployment generation %d",
                published["generation"],
            )


if __name__ == "__main__":
    main()
