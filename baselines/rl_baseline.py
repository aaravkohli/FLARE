"""
baselines/rl_baseline.py — [OPTIONAL]
Baseline RL strategies for research comparison against the DQN agent.

Strategies:
  1. Greedy      — always pick the path with the lowest FL threat score
  2. Static      — always pick Direct (path 0) regardless of scores

Both are evaluated over 1000 episodes against the DronePathEnv.
Metrics: mean episode reward, mean recovery time, mean packet loss proxy.

Usage:
  python baselines/rl_baseline.py
"""

import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from rl.env import DronePathEnv, PATH_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [RL-BASELINE] %(message)s")
logger = logging.getLogger(__name__)

NUM_EPISODES = 200
MAX_STEPS    = 500


def run_strategy(name: str, policy_fn) -> dict:
    """
    Evaluate a policy function over NUM_EPISODES episodes.

    policy_fn(obs, info) -> action (int)
    """
    env = DronePathEnv()
    total_rewards, switches, steps_list = [], [], []

    for ep in range(NUM_EPISODES):
        obs, info = env.reset()
        ep_reward = 0.0
        ep_switches = 0
        prev_action = 0

        for step in range(MAX_STEPS):
            action = policy_fn(obs)
            obs, reward, terminated, _, info = env.step(action)
            ep_reward += reward
            if action != prev_action:
                ep_switches += 1
            prev_action = action
            if terminated:
                break

        total_rewards.append(ep_reward)
        switches.append(ep_switches)
        steps_list.append(step + 1)

    mean_r  = float(np.mean(total_rewards))
    std_r   = float(np.std(total_rewards))
    mean_sw = float(np.mean(switches))

    logger.info(
        "%s — Mean reward: %.3f ± %.3f | Mean path switches: %.1f",
        name, mean_r, std_r, mean_sw,
    )
    return {"strategy": name, "mean_reward": mean_r, "std_reward": std_r, "mean_switches": mean_sw}


def greedy_policy(obs: np.ndarray) -> int:
    """Pick the path with the lowest threat score (first 3 obs dims)."""
    path_scores = obs[:3]
    return int(np.argmin(path_scores))


def static_policy(obs: np.ndarray) -> int:
    """Always select Direct path (action 0)."""
    return 0


def run_baselines():
    logger.info("Evaluating RL baselines over %d episodes, %d steps each.", NUM_EPISODES, MAX_STEPS)
    results = []
    results.append(run_strategy("Greedy (lowest threat)", greedy_policy))
    results.append(run_strategy("Static (always Direct)", static_policy))

    print("\n" + "=" * 60)
    print(f"{'Strategy':<30} {'Mean Reward':>12} {'Std Reward':>12} {'Switches':>10}")
    print("=" * 60)
    for r in results:
        print(f"{r['strategy']:<30} {r['mean_reward']:>12.4f} {r['std_reward']:>12.4f} {r['mean_switches']:>10.1f}")
    print("=" * 60)
    print("(Compare these against DQN agent for publication)")
    return results


if __name__ == "__main__":
    run_baselines()
