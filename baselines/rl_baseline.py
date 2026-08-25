"""Paired seeded routing baselines for comparison with trained policies."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rl.evaluation import evaluate_policy_set


logging.basicConfig(level=logging.INFO, format="%(asctime)s [RL-BASELINE] %(message)s")
logger = logging.getLogger(__name__)

NUM_EPISODES = 200
BASE_SEED = 42_000
BASELINE_POLICIES = (
    "greedy_lowest_threat",
    "myopic_reward",
    "static_direct",
    "random",
)


def run_baselines(
    *,
    num_episodes: int = NUM_EPISODES,
    base_seed: int = BASE_SEED,
) -> dict:
    """Evaluate all baselines on the same synthetic RF trace seeds."""
    logger.info(
        "Evaluating %d paired episodes from base seed %d.",
        num_episodes,
        base_seed,
    )
    result = evaluate_policy_set(
        None,
        n_episodes=num_episodes,
        base_seed=base_seed,
        policy_names=BASELINE_POLICIES,
    )

    print("\n" + "=" * 94)
    print(
        f"{'Strategy':<26} {'Mean reward':>14} {'95% CI ±':>12} "
        f"{'Switches':>12} {'Jammed choices':>16}"
    )
    print("=" * 94)
    for name, metrics in result["policies"].items():
        print(
            f"{name:<26} "
            f"{metrics['mean_episode_reward']:>14.4f} "
            f"{metrics['reward_95ci_half_width']:>12.4f} "
            f"{metrics['mean_switches_per_episode']:>12.1f} "
            f"{metrics['mean_jammed_selections_per_episode']:>16.1f}"
        )
    print("=" * 94)
    return result


if __name__ == "__main__":
    run_baselines()
