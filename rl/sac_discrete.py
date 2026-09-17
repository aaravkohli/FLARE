"""
rl/sac_discrete.py — Discrete Soft Actor-Critic (Discrete SAC)  [FLARE v2]

Implements SOTA Discrete Soft Actor-Critic for routing optimisation.
Includes:
  1. Dual Q-networks (Double Critic) to prevent Q-value overestimation
  2. Actor (Policy) network outputting categorical action probabilities
  3. Automatic entropy coefficient tuning (temperature α)
  4. standard Replay Buffer

References:
  Christodoulou, P. (2019). Soft Actor-Critic for Discrete Action Settings.
  https://arxiv.org/abs/1910.07207

  Haarnoja, T. et al. (2018). Soft Actor-Critic: Off-Policy Maximum Entropy
  Deep Reinforcement Learning with a Stochastic Actor. ICML 2018.

Discrete SAC Objectives:
  Critic Q(s, a) Loss:
    y = r + γ · (1-d) · Σ_a' [ π(a'|s') · (min Q_target(s', a') - α · log π(a'|s')) ]
    L_Q = 0.5 * (Q(s, a) - y)²

  Actor Policy π(a|s) Loss:
    L_π = Σ_a [ π(a|s) · (α · log π(a|s) - min Q(s, a)) ]

  Entropy Temperature α Loss:
    L_α = α · Σ_a [ π(a|s) · (-log π(a|s) - H_target) ]
"""

from __future__ import annotations

import logging
import os
import random
from collections import deque
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Discrete Actor & Critic Networks
# ---------------------------------------------------------------------------

class DiscreteActor(nn.Module):
    """
    Policy network π(a|s) outputting a categorical probability distribution
    over discrete action choices.
    """

    def __init__(self, state_dim: int, action_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, action_dim),
        )

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        logits = self.net(state)
        return F.softmax(logits, dim=-1)

    def evaluate(self, state: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Evaluate actions and return probabilities and log probabilities.
        Ensures numerical stability for log probabilities.
        """
        probs = self.forward(state)
        log_probs = torch.log(probs + 1e-8)
        return probs, log_probs

    def get_action(self, state: torch.Tensor, deterministic: bool = False) -> int:
        """Select action during evaluation or exploration."""
        probs = self.forward(state)
        if deterministic:
            return int(probs.argmax(dim=-1).item())
        dist = torch.distributions.Categorical(probs)
        return int(dist.sample().item())


class DiscreteCritic(nn.Module):
    """
    Q-value network Q(s, a) outputting Q-values for all discrete actions.
    """

    def __init__(self, state_dim: int, action_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, action_dim),
        )

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        return self.net(state)


# ---------------------------------------------------------------------------
# Standard Replay Buffer
# ---------------------------------------------------------------------------

class ReplayBuffer:
    """Standard off-policy transition buffer."""

    def __init__(self, max_size: int = 20000):
        self.buffer: deque = deque(maxlen=max_size)

    def push(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        self.buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        batch = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)
        return (
            np.array(states, dtype=np.float32),
            np.array(actions, dtype=np.int64),
            np.array(rewards, dtype=np.float32),
            np.array(next_states, dtype=np.float32),
            np.array(dones, dtype=np.float32),
        )

    def __len__(self) -> int:
        return len(self.buffer)


# ---------------------------------------------------------------------------
# Discrete SAC Agent
# ---------------------------------------------------------------------------

class DiscreteSACAgent:
    """
    Self-contained Discrete Soft Actor-Critic routing agent.
    Exposes a clean SB3-like interface (`predict`, `save`, `load`).
    """

    def __init__(
        self,
        state_dim: int,
        action_dim: int,
        hidden_dim: int = 128,
        gamma: float = 0.99,
        tau: float = 0.005,
        policy_lr: float = 0.0003,
        critic_lr: float = 0.0003,
        alpha_lr: float = 0.0003,
        initial_alpha: float = 0.2,
        target_entropy_ratio: float = 0.98,
        device: Optional[str] = None,
    ):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.gamma = gamma
        self.tau = tau
        self.device = torch.device(device if device else ("cuda" if torch.cuda.is_available() else "cpu"))

        # Actor
        self.actor = DiscreteActor(state_dim, action_dim, hidden_dim).to(self.device)
        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=policy_lr)

        # Critic Critics (Double Q)
        self.critic1 = DiscreteCritic(state_dim, action_dim, hidden_dim).to(self.device)
        self.critic2 = DiscreteCritic(state_dim, action_dim, hidden_dim).to(self.device)
        self.critic1_target = DiscreteCritic(state_dim, action_dim, hidden_dim).to(self.device)
        self.critic2_target = DiscreteCritic(state_dim, action_dim, hidden_dim).to(self.device)

        # Copy initial weights
        self.critic1_target.load_state_dict(self.critic1.state_dict())
        self.critic2_target.load_state_dict(self.critic2.state_dict())

        self.critic_optimizer = torch.optim.Adam(
            list(self.critic1.parameters()) + list(self.critic2.parameters()),
            lr=critic_lr,
        )

        # Entropy coefficient tuning
        # Target entropy = target_entropy_ratio * log(|A|)
        self.target_entropy = -target_entropy_ratio * np.log(action_dim)
        self.log_alpha = torch.tensor(np.log(initial_alpha), requires_grad=True, device=self.device)
        self.alpha_optimizer = torch.optim.Adam([self.log_alpha], lr=alpha_lr)

    @property
    def alpha(self) -> float:
        return float(self.log_alpha.exp().item())

    def predict(self, state: np.ndarray, deterministic: bool = True) -> Tuple[int, None]:
        """Exposes SB3-like prediction interface."""
        self.actor.eval()
        with torch.no_grad():
            state_t = torch.tensor(state, dtype=torch.float32, device=self.device).unsqueeze(0)
            action = self.actor.get_action(state_t, deterministic=deterministic)
        return action, None

    def update_parameters(self, replay_buffer: ReplayBuffer, batch_size: int) -> dict:
        """Perform one training update step."""
        self.actor.train()
        self.critic1.train()
        self.critic2.train()

        states, actions, rewards, next_states, dones = replay_buffer.sample(batch_size)

        states_t = torch.tensor(states, device=self.device)
        actions_t = torch.tensor(actions, device=self.device).unsqueeze(1)
        rewards_t = torch.tensor(rewards, device=self.device).unsqueeze(1)
        next_states_t = torch.tensor(next_states, device=self.device)
        dones_t = torch.tensor(dones, device=self.device).unsqueeze(1)

        alpha = self.log_alpha.exp()

        # -------------------------------------------------------------------
        # 1. Critic Update
        # -------------------------------------------------------------------
        with torch.no_grad():
            # Get action probabilities for next state: π(a'|s')
            next_state_probs, next_state_log_probs = self.actor.evaluate(next_states_t)

            # Get target Q-values: min Q_target(s', a')
            q1_next = self.critic1_target(next_states_t)
            q2_next = self.critic2_target(next_states_t)
            min_q_next = torch.min(q1_next, q2_next)

            # Target V: value expectation with entropy penalty
            # V(s') = Σ_a' [ π(a'|s') · (min Q(s', a') - α · log π(a'|s')) ]
            next_v = (next_state_probs * (min_q_next - alpha * next_state_log_probs)).sum(dim=-1, keepdim=True)

            # Target Q-value: y = r + γ·(1-d)·V(s')
            target_q = rewards_t + (1.0 - dones_t) * self.gamma * next_v

        # Current Q-values
        curr_q1 = self.critic1(states_t).gather(1, actions_t)
        curr_q2 = self.critic2(states_t).gather(1, actions_t)

        critic_loss = F.mse_loss(curr_q1, target_q) + F.mse_loss(curr_q2, target_q)

        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()

        # -------------------------------------------------------------------
        # 2. Actor Update
        # -------------------------------------------------------------------
        probs, log_probs = self.actor.evaluate(states_t)

        with torch.no_grad():
            q1 = self.critic1(states_t)
            q2 = self.critic2(states_t)
            min_q = torch.min(q1, q2)

        # Actor Loss: Σ_a [ π(a|s) · (α · log π(a|s) - min Q(s, a)) ]
        actor_loss = (probs * (alpha.detach() * log_probs - min_q)).sum(dim=-1).mean()

        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        self.actor_optimizer.step()

        # -------------------------------------------------------------------
        # 3. Entropy Coefficient (Alpha) Update
        # -------------------------------------------------------------------
        # Alpha Loss: α · Σ_a [ π(a|s) · (-log π(a|s) - H_target) ]
        entropy = -(probs * log_probs).sum(dim=-1).mean()
        alpha_loss = -(self.log_alpha * (log_probs.detach() + self.target_entropy).mean())

        self.alpha_optimizer.zero_grad()
        alpha_loss.backward()
        self.alpha_optimizer.step()

        # Soft update target network weights
        self._soft_update(self.critic1, self.critic1_target)
        self._soft_update(self.critic2, self.critic2_target)

        return {
            "critic_loss": critic_loss.item(),
            "actor_loss": actor_loss.item(),
            "alpha_loss": alpha_loss.item(),
            "alpha": alpha.item(),
            "entropy": entropy.item(),
        }

    def _soft_update(self, source: nn.Module, target: nn.Module) -> None:
        """Target_param = (1 - τ) * Target_param + τ * Source_param"""
        for param, target_param in zip(source.parameters(), target.parameters()):
            target_param.data.copy_(
                (1.0 - self.tau) * target_param.data + self.tau * param.data
            )

    def save(self, filepath: str) -> None:
        """Save networks and optimizers."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        checkpoint = {
            "actor_state_dict": self.actor.state_dict(),
            "critic1_state_dict": self.critic1.state_dict(),
            "critic2_state_dict": self.critic2.state_dict(),
            "critic1_target_state_dict": self.critic1_target.state_dict(),
            "critic2_target_state_dict": self.critic2_target.state_dict(),
            "log_alpha": self.log_alpha,
            "actor_opt": self.actor_optimizer.state_dict(),
            "critic_opt": self.critic_optimizer.state_dict(),
            "alpha_opt": self.alpha_optimizer.state_dict(),
        }
        torch.save(checkpoint, filepath)
        logger.info("[SAC] Saved model state → %s", filepath)

    def load(self, filepath: str) -> None:
        """Load networks and optimizers."""
        if not Path(filepath).exists():
            raise FileNotFoundError(f"No checkpoint found at {filepath}")
        checkpoint = torch.load(filepath, map_location=self.device)
        self.actor.load_state_dict(checkpoint["actor_state_dict"])
        self.critic1.load_state_dict(checkpoint["critic1_state_dict"])
        self.critic2.load_state_dict(checkpoint["critic2_state_dict"])
        self.critic1_target.load_state_dict(checkpoint["critic1_target_state_dict"])
        self.critic2_target.load_state_dict(checkpoint["critic2_target_state_dict"])
        self.log_alpha.data.copy_(checkpoint["log_alpha"].data)
        self.actor_optimizer.load_state_dict(checkpoint["actor_opt"])
        self.critic_optimizer.load_state_dict(checkpoint["critic_opt"])
        self.alpha_optimizer.load_state_dict(checkpoint["alpha_opt"])
        logger.info("[SAC] Loaded model state from %s", filepath)


# ---------------------------------------------------------------------------
# Self-Test Hook
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = DiscreteSACAgent(state_dim=14, action_dim=3)
    # Mock transition
    state = np.random.randn(14).astype(np.float32)
    a, _ = agent.predict(state)
    assert a in [0, 1, 2], f"Expected action in [0, 1, 2], got {a}"

    # Train step check
    buffer = ReplayBuffer()
    for _ in range(100):
        buffer.push(state, a, 1.0, state, False)

    metrics = agent.update_parameters(buffer, batch_size=32)
    assert "critic_loss" in metrics
    print(f"Discrete SAC agent self-test passed. Loss: {metrics['critic_loss']:.4f}")
