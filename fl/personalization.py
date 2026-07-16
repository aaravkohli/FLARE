"""
fl/personalization.py — Personalized Federated Learning (pFedMe)  [FLARE v2]

Implements personalized FL using the Moreau Envelope approach (pFedMe):
  - Global model θ is learned via standard FL (federated)
  - Each client maintains a personal model w_i (local, not sent to server)
  - w_i is found by solving the proximal problem:
      w_i* = argmin_w [ f_i(w) + (λ/2) ||w - θ||² ]
  - The proximal term anchors w_i close to θ, preventing excessive divergence

This results in personalized models that are:
  1. Better than global-only (adapted to local data distribution)
  2. Better than local-only (anchored by global knowledge)

Architecture:
  Option A (head_only=True):  Only personalize output heads (head_threat, head_attack)
                               while keeping the BiLSTM backbone global.
  Option B (head_only=False): Full model personalization with proximal regularization.

Reference:
  T. Dinh, C. et al. (2020). Personalized Federated Learning with Moreau Envelopes.
  NeurIPS 2020. https://arxiv.org/abs/2006.08848

  Li, T. et al. (2020). Federated Optimization in Heterogeneous Networks (FedProx).
  MLSys 2020. https://arxiv.org/abs/1812.06127

Moreau Envelope Objective:
    F_i(w_i; θ) = f_i(w_i) + (λ/2) ||w_i - θ||²
    
    w_i* = argmin_w F_i(w; θ)
    
    Gradient:
    ∇F_i(w; θ) = ∇f_i(w) + λ(w - θ)

Global Model Update (server aggregation):
    θ ← θ - η · (1/N) Σ_i λ(θ - w_i*)
          = (1 - ηλ) θ + (ηλ/N) Σ_i w_i*

Computational overhead:
  local_adapt_steps additional gradient steps per client.
  Memory: 1 extra model copy per client for the personal model.

Unit-test hooks: run `python -m fl.personalization` for self-test.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Proximal Loss Component
# ---------------------------------------------------------------------------

def proximal_loss(
    model: nn.Module,
    global_weights: List[np.ndarray],
    lambda_prox: float,
) -> torch.Tensor:
    """
    Compute the proximal regularization term: (λ/2) ||w - θ||²

    This is the Moreau envelope term that anchors the personal model
    to the global model θ.

    FedProx Proximal Term (Li et al. 2020):
        h(w; w_t) = (μ/2) ||w - w_t||²

    Note: pFedMe uses this same term but interprets it differently:
    in pFedMe, λ is larger and the personalized model truly adapts,
    whereas FedProx uses small μ just for stability.

    Args:
        model:          Personal model w_i (current parameters).
        global_weights: Global model θ as list of numpy arrays.
        lambda_prox:    Proximal regularization strength λ.

    Returns:
        Scalar proximal loss tensor (to be added to f_i(w)).
    """
    prox_loss = torch.tensor(0.0, requires_grad=True)
    param_idx = 0
    for param in model.parameters():
        if param_idx >= len(global_weights):
            break
        global_param = torch.tensor(
            global_weights[param_idx],
            dtype=param.dtype,
            device=param.device,
        )
        prox_loss = prox_loss + torch.sum((param - global_param) ** 2)
        param_idx += 1

    return (lambda_prox / 2.0) * prox_loss


# ---------------------------------------------------------------------------
# Per-Client Personalization Manager
# ---------------------------------------------------------------------------

class PersonalizationManager:
    """
    Manages per-client personalized model adaptation.

    Responsibilities:
      - Store personal model per client (or just the personalized head weights)
      - Run local adaptation steps (proximal minimization)
      - Save/load personalized models to disk
      - Report delta between personal and global model

    Usage (in DroneFlClient.fit()):
        persona = PersonalizationManager(client_id, model_cfg, lambda_prox, ...)
        persona.load_or_init(global_weights)
        persona.adapt(X, y_threat, y_attack, global_weights)
        personal_weights = persona.get_weights()
    """

    def __init__(
        self,
        client_id: str,
        model_builder,           # callable: build_model(cfg) → BiLSTMAttention
        model_cfg: dict,
        lambda_prox: float = 0.1,
        local_adapt_steps: int = 3,
        head_only: bool = True,
        save_dir: Optional[str] = None,
        device: Optional[torch.device] = None,
        enabled: bool = True,
    ):
        self.client_id = client_id
        self.model_builder = model_builder
        self.model_cfg = model_cfg
        self.lambda_prox = lambda_prox
        self.local_adapt_steps = local_adapt_steps
        self.head_only = head_only
        self.save_dir = Path(save_dir) if save_dir else None
        self.device = device or torch.device("cpu")
        self.enabled = enabled
        self._personal_model: Optional[nn.Module] = None

    def _build_personal_model(self) -> nn.Module:
        return self.model_builder(self.model_cfg).to(self.device)

    def _personal_model_path(self) -> Optional[Path]:
        if self.save_dir is None:
            return None
        self.save_dir.mkdir(parents=True, exist_ok=True)
        return self.save_dir / f"{self.client_id}_personal.pth"

    def load_or_init(self, global_weights: List[np.ndarray]) -> nn.Module:
        """
        Load saved personal model or initialize from global weights.

        Returns the personal model (on device).
        """
        if not self.enabled:
            # In disabled mode, personal model = global model
            from fl.model import set_model_weights
            model = self._build_personal_model()
            set_model_weights(model, global_weights)
            self._personal_model = model
            return model

        model = self._build_personal_model()
        path = self._personal_model_path()

        if path is not None and path.exists():
            try:
                state_dict = torch.load(path, map_location=self.device)
                model.load_state_dict(state_dict, strict=False)
                logger.info("[Persona] Loaded personal model for %s from %s", self.client_id, path)
            except Exception as exc:
                logger.warning("[Persona] Failed to load personal model: %s. Reinitializing.", exc)
                from fl.model import set_model_weights
                set_model_weights(model, global_weights)
        else:
            from fl.model import set_model_weights
            set_model_weights(model, global_weights)
            logger.debug("[Persona] Initialized personal model for %s from global weights.", self.client_id)

        self._personal_model = model
        return model

    def adapt(
        self,
        X: torch.Tensor,
        y_threat: torch.Tensor,
        y_attack: torch.Tensor,
        global_weights: List[np.ndarray],
        batch_size: int = 64,
        lr: float = 0.001,
    ) -> dict:
        """
        Run local_adapt_steps gradient steps to personalize w_i.

        Optimization:
            w_i ← w_i - η · ∇F_i(w_i; θ)
            where ∇F_i = ∇f_i(w_i) + λ(w_i - θ)

        If head_only=True, only the output heads are adapted.

        Args:
            X, y_threat, y_attack: Local training tensors.
            global_weights:         Current global model θ.
            batch_size:             Mini-batch size.
            lr:                     Learning rate for adaptation.

        Returns:
            Metrics dict with personal loss and personalization delta.
        """
        if not self.enabled or self._personal_model is None:
            return {"personal_loss": 0.0, "persona_delta": 0.0}

        model = self._personal_model

        # If head_only, freeze BiLSTM layers
        if self.head_only:
            for name, param in model.named_parameters():
                if "head_" not in name:
                    param.requires_grad_(False)
            trainable_params = [p for p in model.parameters() if p.requires_grad]
        else:
            for param in model.parameters():
                param.requires_grad_(True)
            trainable_params = list(model.parameters())

        optimizer = torch.optim.Adam(trainable_params, lr=lr)
        bce_loss = nn.BCELoss()
        ce_loss = nn.CrossEntropyLoss()

        dataset = torch.utils.data.TensorDataset(X, y_threat, y_attack)
        loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

        model.train()
        total_loss = 0.0
        steps = 0

        for step in range(self.local_adapt_steps):
            for xb, yb_threat, yb_attack in loader:
                xb = xb.to(self.device)
                yb_threat = yb_threat.to(self.device)
                yb_attack = yb_attack.to(self.device)

                out = model(xb)
                task_loss = (
                    2.0 * bce_loss(out.path_scores, yb_threat)
                    + 0.5 * ce_loss(out.attack_logits, yb_attack)
                )

                # Add proximal term: (λ/2)||w - θ||²
                prox = proximal_loss(model, global_weights, self.lambda_prox)
                loss = task_loss + prox

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                total_loss += task_loss.item()
                steps += 1

        # Re-enable all parameters for future use
        for param in model.parameters():
            param.requires_grad_(True)

        # Measure personalization delta from global model
        from fl.model import get_model_weights
        personal_weights = get_model_weights(model)
        delta = float(sum(
            np.linalg.norm(pw - gw)
            for pw, gw in zip(personal_weights, global_weights)
        ))

        avg_loss = total_loss / max(steps, 1)
        logger.info(
            "[Persona] %s adapted: steps=%d, loss=%.4f, delta_from_global=%.4f",
            self.client_id, steps, avg_loss, delta,
        )
        return {"personal_loss": avg_loss, "persona_delta": delta}

    def save(self) -> None:
        """Save personal model to disk."""
        if self._personal_model is None:
            return
        path = self._personal_model_path()
        if path is not None:
            torch.save(self._personal_model.state_dict(), path)
            logger.debug("[Persona] Saved personal model for %s → %s", self.client_id, path)

    def get_weights(self) -> List[np.ndarray]:
        """Return personal model weights as numpy arrays."""
        if self._personal_model is None:
            return []
        from fl.model import get_model_weights
        return get_model_weights(self._personal_model)

    def personalization_gap(self, global_weights: List[np.ndarray]) -> float:
        """L2 distance between personal and global model."""
        personal = self.get_weights()
        if not personal:
            return 0.0
        return float(sum(
            np.linalg.norm(pw - gw)
            for pw, gw in zip(personal, global_weights)
        ))


# ---------------------------------------------------------------------------
# Server-side: Personalization aggregation helper
# ---------------------------------------------------------------------------

def aggregate_for_personalization(
    client_personal_weights: List[List[np.ndarray]],
    global_weights: List[np.ndarray],
    lambda_prox: float,
    learning_rate: float = 0.01,
) -> List[np.ndarray]:
    """
    Server-side global model update for pFedMe.

    pFedMe Server Update (T. Dinh et al. 2020, Eq. 4):
        θ_{t+1} = (1 - η·λ) θ_t + (η·λ/K) Σ_{i=1}^{K} w_i*

    where w_i* are personalized models returned by clients.

    This is equivalent to moving the global model toward the
    mean of personalized models.

    Args:
        client_personal_weights: List of personalized model weights per client.
        global_weights:          Current global model θ_t.
        lambda_prox:             Proximal regularization λ.
        learning_rate:           Server learning rate η.

    Returns:
        Updated global model weights θ_{t+1}.
    """
    K = len(client_personal_weights)
    if K == 0:
        return global_weights

    # Mean of personalized models: (1/K) Σ w_i*
    mean_personal = []
    for layer_idx in range(len(global_weights)):
        layer_mean = np.mean(
            [cpw[layer_idx] for cpw in client_personal_weights], axis=0
        )
        mean_personal.append(layer_mean)

    # pFedMe update: θ_{t+1} = (1 - η·λ)·θ + (η·λ)·mean_personal
    coef_old = 1.0 - learning_rate * lambda_prox
    coef_new = learning_rate * lambda_prox
    updated = [
        coef_old * gw + coef_new * mp
        for gw, mp in zip(global_weights, mean_personal)
    ]
    logger.info(
        "[Persona] Server pFedMe update: K=%d, η·λ=%.4f", K, learning_rate * lambda_prox
    )
    return updated


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_proximal_loss():
    """Verify proximal loss is zero when model matches global weights."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from fl.model import build_model, get_model_weights

    cfg = {"input_features": 5, "sequence_len": 10, "hidden_size": 16,
           "num_layers": 1, "dropout": 0.0, "num_paths": 3, "num_attack_classes": 5}
    model = build_model(cfg)
    global_weights = get_model_weights(model)  # same weights → prox should be ~0

    prox = proximal_loss(model, global_weights, lambda_prox=0.1)
    assert prox.item() < 1e-6, f"Proximal loss should be 0 when model = global, got {prox.item()}"
    print(f"[PASS] Proximal loss when w=θ: {prox.item():.8f}")


def _test_personalization_manager():
    """Verify PersonalizationManager initializes and adapts correctly."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from fl.model import build_model, get_model_weights

    cfg = {"input_features": 5, "sequence_len": 10, "hidden_size": 16,
           "num_layers": 1, "dropout": 0.0, "num_paths": 3, "num_attack_classes": 5}

    manager = PersonalizationManager(
        client_id="test_drone",
        model_builder=build_model,
        model_cfg=cfg,
        lambda_prox=0.1,
        local_adapt_steps=2,
        head_only=True,
        enabled=True,
    )

    model = build_model(cfg)
    global_weights = get_model_weights(model)

    manager.load_or_init(global_weights)

    X = torch.randn(32, 10, 5)
    y_threat = torch.rand(32, 3)
    y_attack = torch.randint(0, 5, (32,))

    metrics = manager.adapt(X, y_threat, y_attack, global_weights)
    assert "personal_loss" in metrics
    assert metrics["personal_loss"] >= 0.0
    print(f"[PASS] Personalization adapt: loss={metrics['personal_loss']:.4f}, delta={metrics['persona_delta']:.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_proximal_loss()
    _test_personalization_manager()
    print("All fl/personalization.py tests passed.")
