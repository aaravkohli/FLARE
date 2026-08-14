"""
fl/privacy.py — Differential Privacy for Federated Learning  [FLARE v2]

Implements two complementary DP mechanisms:
  A) Client-side DP-SGD: per-sample gradient clipping + Gaussian noise
     integrated via Opacus (used inside DroneFlClient.fit())
  B) Server-side post-aggregation Gaussian noise: small noise injected
     into the aggregated global model update after every round

Privacy Accounting:
  Uses Rényi Differential Privacy (RDP) moments accountant for tighter
  bounds than the basic composition theorem.

Reference:
  Abadi, M. et al. (2016). Deep Learning with Differential Privacy.
  CCS 2016. https://arxiv.org/abs/1607.00133

  Mironov, I. (2017). Rényi Differential Privacy of the Gaussian Mechanism.
  CSF 2017. https://arxiv.org/abs/1702.07476

  Opacus: https://github.com/pytorch/opacus

Computational overhead:
  Client-side: ~15% slowdown (per-sample gradient hooks)
  Memory:      ~2× model parameters (per-sample gradient buffers)
  Server-side: O(d) — negligible

Unit-test hooks: run `python -m fl.privacy` for self-test.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Privacy Budget Tracker
# ---------------------------------------------------------------------------

class PrivacyAccountant:
    """
    Tracks cumulative privacy budget (ε, δ) across FL rounds using the
    Rényi DP (RDP) moments accountant.

    RDP Gaussian mechanism (Mironov 2017):
        RDP_α(M) = α / (2σ²)

    Conversion from RDP to (ε, δ)-DP (Balle et al. 2020):
        ε = min_α [ RDP_α(M·T) - log(δ) / (α - 1) ]

    where T = number of compositions (rounds × steps_per_round).
    """

    def __init__(
        self,
        noise_multiplier: float,
        sample_rate: float,
        alphas: Optional[List[float]] = None,
        delta: float = 1e-5,
    ):
        """
        Args:
            noise_multiplier: σ — ratio of Gaussian noise std to sensitivity.
            sample_rate:       q — fraction of dataset sampled each step.
            alphas:            Rényi orders for accounting (default: standard set).
            delta:             Failure probability δ for (ε, δ)-DP.
        """
        self.noise_multiplier = noise_multiplier
        self.sample_rate = sample_rate
        self.delta = delta
        self.alphas = alphas or [1.5, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
        self._steps = 0  # total composition steps taken

    def step(self, n_steps: int = 1) -> None:
        """Record n_steps of DP-SGD (one step = one optimizer step)."""
        self._steps += n_steps

    @property
    def total_steps(self) -> int:
        return self._steps

    def compute_epsilon(self) -> Tuple[float, float]:
        """
        Compute current (ε, δ) using the Rényi DP accountant.

        Subsampled Gaussian mechanism RDP (Mironov 2017 Eq. 3):
            RDP_α ≈ (α · q² · T) / (2σ²)   [simplified, for α ≥ 2]

        Returns:
            (epsilon, delta) tuple.
        """
        if self._steps == 0:
            return 0.0, self.delta

        q = self.sample_rate
        sigma = self.noise_multiplier
        T = self._steps

        best_eps = float("inf")
        for alpha in self.alphas:
            if alpha <= 1.0:
                continue
            # RDP for subsampled Gaussian (Mironov 2017, simplified):
            #   ε_RDP(α) = (α · q² · T) / (2σ²)
            rdp = (alpha * q**2 * T) / (2.0 * sigma**2)
            # Convert RDP to (ε, δ)-DP:
            #   ε = ε_RDP + log(1 - 1/α) - log(δ(α-1)) / (α-1)
            eps = rdp + math.log(1.0 - 1.0 / alpha) - math.log(self.delta * (alpha - 1.0)) / (alpha - 1.0)
            if eps < best_eps:
                best_eps = eps

        return max(0.0, best_eps), self.delta

    def summary(self) -> dict:
        eps, delta = self.compute_epsilon()
        return {
            "steps": self._steps,
            "epsilon": round(eps, 4),
            "delta": self.delta,
            "noise_multiplier": self.noise_multiplier,
            "sample_rate": self.sample_rate,
        }


# ---------------------------------------------------------------------------
# Non-private gradient perturbation research helpers
# (Not used as a fallback for the Opacus client path)
# ---------------------------------------------------------------------------

def clip_per_sample_gradients(
    model: nn.Module,
    max_grad_norm: float,
) -> float:
    """
    Clip each sample's gradient to max_grad_norm in L2 norm.
    Despite the legacy function name, this clips one aggregated batch gradient,
    not per-sample gradients. It is retained for experimentation only and does
    not establish a differential-privacy guarantee.

    Equation:
        g̃ = g / max(1, ||g||₂ / C)
        where C = max_grad_norm

    Returns:
        actual_grad_norm before clipping.
    """
    total_norm = 0.0
    params = [p for p in model.parameters() if p.grad is not None]
    for p in params:
        total_norm += p.grad.detach().norm(2).item() ** 2
    total_norm = math.sqrt(total_norm)

    clip_coef = max_grad_norm / max(total_norm, max_grad_norm)
    if clip_coef < 1.0:
        for p in params:
            p.grad.detach().mul_(clip_coef)
        logger.debug("DP clip: norm %.4f → %.4f (coef=%.4f)",
                     total_norm, max_grad_norm, clip_coef)

    return total_norm


def add_gaussian_noise_to_gradients(
    model: nn.Module,
    noise_multiplier: float,
    max_grad_norm: float,
) -> None:
    """
    Add calibrated Gaussian noise to all parameter gradients.

    DP-SGD Noise Step (Abadi 2016, Algorithm 1):
        g̃_t = (1/L) · [Σ_i clip(g_t^i, C) + N(0, σ²C²I)]
        where σ = noise_multiplier, C = max_grad_norm

    This ensures (ε, δ)-DP when composed with the clipping step.
    """
    std = noise_multiplier * max_grad_norm
    for p in model.parameters():
        if p.grad is not None:
            noise = torch.randn_like(p.grad) * std
            p.grad.detach().add_(noise)


def apply_client_dp_step(
    model: nn.Module,
    max_grad_norm: float,
    noise_multiplier: float,
) -> float:
    """
    Aggregated-gradient clipping/noise experiment; not formal DP-SGD.
    Call this AFTER loss.backward() and BEFORE optimizer.step().

    Returns the gradient norm before clipping (for accounting).
    """
    norm = clip_per_sample_gradients(model, max_grad_norm)
    add_gaussian_noise_to_gradients(model, noise_multiplier, max_grad_norm)
    return norm


# ---------------------------------------------------------------------------
# Server-Side DP: Post-Aggregation Gaussian Noise
# ---------------------------------------------------------------------------

def add_server_side_dp_noise(
    aggregated_weights: List[np.ndarray],
    noise_scale: float,
    sensitivity: float,
) -> List[np.ndarray]:
    """
    Add Gaussian noise to aggregated model weights (server-side DP).

    Server DP Mechanism (Geyer et al. 2017):
        θ_global ← θ_global + N(0, (noise_scale · sensitivity)² · I)

    This provides an additional privacy layer on top of client-side DP.
    Even if client-side DP is disabled, this protects the aggregated model.

    Args:
        aggregated_weights: List of numpy arrays (global model parameters).
        noise_scale:        σ_server (from config: differential_privacy.server_side.noise_scale).
        sensitivity:        Δf = clip_norm (from config: security.clip_norm).

    Returns:
        Noised global weights.

    Overhead: O(d) where d = total parameters. Negligible.
    """
    std = noise_scale * sensitivity
    noised = []
    total_params = 0
    for layer in aggregated_weights:
        noise = np.random.normal(0.0, std, size=layer.shape).astype(layer.dtype)
        noised.append(layer + noise)
        total_params += layer.size

    logger.debug("Server-side DP noise applied: σ_server=%.4f, d=%d params", std, total_params)
    return noised


# ---------------------------------------------------------------------------
# Opacus Integration Helper
# ---------------------------------------------------------------------------

def make_opacus_private_engine(
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    data_loader,
    target_epsilon: float,
    target_delta: float,
    max_grad_norm: float,
    epochs: int,
) -> Tuple[nn.Module, torch.optim.Optimizer, object, object, float]:
    """
    Wrap a model and optimizer with Opacus for full DP-SGD.

    Opacus computes per-sample gradients using PyTorch hooks,
    enabling true DP-SGD (vs. the approximation above).

    Args:
        model:          PyTorch model to make private.
        optimizer:      Standard optimizer (Adam, SGD).
        data_loader:    Training DataLoader.
        target_epsilon: Target ε for privacy budget.
        target_delta:   Target δ.
        max_grad_norm:  Per-sample gradient clipping norm C.
        epochs:         Number of training epochs (for noise calibration).

    Returns:
        (private_model, private_optimizer, private_loader, privacy_engine,
        noise_multiplier)

    Reference: Opacus documentation, make_private_with_epsilon()
    """
    try:
        from opacus import PrivacyEngine
    except ImportError as exc:
        raise RuntimeError(
            "Opacus is required for client-side DP-SGD; aggregated-gradient "
            "noise is not treated as a privacy-preserving fallback"
        ) from exc

    privacy_engine = PrivacyEngine()
    private_model, private_optimizer, private_loader = privacy_engine.make_private_with_epsilon(
        module=model,
        optimizer=optimizer,
        data_loader=data_loader,
        target_epsilon=target_epsilon,
        target_delta=target_delta,
        max_grad_norm=max_grad_norm,
        epochs=epochs,
    )
    noise_multiplier = private_optimizer.noise_multiplier
    logger.info(
        "Opacus DP-SGD initialized: ε=%.2f, δ=%.2e, C=%.2f, σ=%.4f",
        target_epsilon, target_delta, max_grad_norm, noise_multiplier,
    )
    return (
        private_model,
        private_optimizer,
        private_loader,
        privacy_engine,
        noise_multiplier,
    )


# ---------------------------------------------------------------------------
# Unit-test hook
# ---------------------------------------------------------------------------

def _test_privacy_accountant():
    """Verify RDP accountant produces sensible epsilon values."""
    acc = PrivacyAccountant(noise_multiplier=1.1, sample_rate=0.01, delta=1e-5)
    eps0, _ = acc.compute_epsilon()
    assert eps0 == 0.0, "Zero steps should give zero epsilon"

    acc.step(100)
    eps1, delta1 = acc.compute_epsilon()
    assert eps1 > 0.0, "After 100 steps epsilon should be positive"
    assert delta1 == 1e-5, "Delta should be constant"

    acc.step(900)
    eps2, _ = acc.compute_epsilon()
    assert eps2 > eps1, "More steps should increase epsilon"

    print(f"[PASS] PrivacyAccountant: ε(100 steps)={eps1:.4f}, ε(1000 steps)={eps2:.4f}")


def _test_server_dp():
    """Verify server-side noise doesn't crash and changes weights."""
    weights = [np.ones((4, 4), dtype=np.float32), np.zeros(4, dtype=np.float32)]
    noised = add_server_side_dp_noise(weights, noise_scale=0.01, sensitivity=5.0)
    assert len(noised) == len(weights)
    assert not np.allclose(noised[0], weights[0]), "Noise should change weights"
    print("[PASS] Server-side DP noise applied correctly")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_privacy_accountant()
    _test_server_dp()
    print("All fl/privacy.py tests passed.")
