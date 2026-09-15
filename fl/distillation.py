"""FedDF-style server ensemble distillation for FLARE.

Implements the model-fusion method described by Lin et al. (2020):
  1. Collect K client models {θ_1, ..., θ_K} after a round
  2. Generate synthetic RF proxy dataset (unlabelled, server-side)
  3. Compute ensemble soft labels: q_x = (1/K) Σ_k softmax(θ_k(x) / T)
  4. Distill the global model θ using KL divergence loss:
        L_KD = KL(q_x || softmax(θ(x) / T))
  5. Optionally mix with hard cross-entropy labels (α blending)

In FLARE every teacher currently has the same BiLSTM architecture. The method
is evaluated as a possible mitigation for non-IID client drift; improvement is
an experimental question, not an assumption made by this module.

Reference:
  Lin, T. et al. (2020). Ensemble Distillation for Robust Model Fusion in
  Federated Learning. NeurIPS 2020. https://arxiv.org/abs/2006.07242

  Hinton, G. et al. (2015). Distilling the Knowledge in a Neural Network.
  NIPS 2015 Workshop. https://arxiv.org/abs/1503.02531

KD Objective (FedDF):
    L_KD(θ) = α · KL(ensemble_soft(x) || model_soft(x))
             + (1-α) · CE(y_hard, model_logits(x))

    where:
        ensemble_soft(x) = (1/K) Σ_k softmax(f_k(x) / T)
        model_soft(x) = softmax(f_θ(x) / T)
        T = temperature (higher → softer targets, more knowledge transfer)

Proxy Dataset:
  Uses the existing synthetic RF data generator — same feature space as
  client data, no real data required at the server.

Computational overhead is K teacher forward passes per proxy batch plus the
configured student optimization epochs. It runs on the configured round
interval, and no fixed percentage-overhead claim is made here.

Unit-test hooks: run `python -m fl.distillation` for self-test.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

from fl.data import correlated_temporal_sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthetic Proxy Dataset Generator
# ---------------------------------------------------------------------------

def generate_proxy_dataset(
    n_samples: int = 500,
    seq_len: int = 10,
    n_features: int = 5,
    seed: int = 42,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Generate synthetic RF proxy data for server-side knowledge distillation.

    The proxy dataset is UNLABELLED — we only need the features X for FedDF,
    because the "labels" come from the ensemble of client models.

    Generates diverse RF conditions covering the full feature space:
      - Normal conditions (RSSI: -75 to -40 dBm, PDR: 0.7-1.0)
      - Jamming conditions (RSSI: -110 to -85 dBm, PDR: 0.0-0.4)

    Args:
        n_samples:  Number of proxy sequences to generate.
        seq_len:    Sequence length (must match BiLSTM input).
        n_features: Number of features (5: rssi, pdr, sinr, latency, pkt_loss).
        seed:       Random seed for reproducibility.

    Returns:
        (X, indices): X is [n_samples, seq_len, n_features] float tensor,
                      indices is dummy integer tensor (unused — for DataLoader compat).
    """
    rng = np.random.default_rng(seed=seed)
    X_list = []

    mins = np.array([-120.0, 0.0, -10.0, 0.0, 0.0], dtype=np.float32)
    maxs = np.array([-20.0, 1.0, 30.0, 1000.0, 1.0], dtype=np.float32)

    # Deterministically cover four operational RF environments instead of
    # drawing only a binary normal/jammed proxy population.
    environments = ("open_terrain", "urban", "far_from_jammer", "near_jammer")
    for sample_index in range(n_samples):
        environment = environments[sample_index % len(environments)]
        jammed = environment == "near_jammer"
        if jammed:
            rssi = rng.uniform(-110.0, -85.0)
            pdr = rng.uniform(0.0, 0.4)
            sinr = rng.uniform(-5.0, 5.0)
            latency = rng.uniform(200.0, 800.0)
            pkt_loss = rng.uniform(0.3, 0.9)
        elif environment == "urban":
            rssi = rng.uniform(-90.0, -60.0)
            pdr = rng.uniform(0.45, 0.85)
            sinr = rng.uniform(2.0, 15.0)
            latency = rng.uniform(60.0, 260.0)
            pkt_loss = rng.uniform(0.08, 0.35)
        elif environment == "far_from_jammer":
            rssi = rng.uniform(-88.0, -65.0)
            pdr = rng.uniform(0.60, 0.92)
            sinr = rng.uniform(5.0, 20.0)
            latency = rng.uniform(30.0, 160.0)
            pkt_loss = rng.uniform(0.03, 0.20)
        else:
            rssi = rng.uniform(-75.0, -40.0)
            pdr = rng.uniform(0.7, 1.0)
            sinr = rng.uniform(10.0, 30.0)
            latency = rng.uniform(5.0, 80.0)
            pkt_loss = rng.uniform(0.0, 0.1)

        feats = np.array([rssi, pdr, sinr, latency, pkt_loss], dtype=np.float32)
        feats_norm = (feats - mins) / (maxs - mins + 1e-8)
        feats_norm = np.clip(feats_norm, 0.0, 1.0)
        seq = correlated_temporal_sequence(feats_norm, seq_len, rng)
        X_list.append(seq)

    X = torch.tensor(np.stack(X_list), dtype=torch.float32)
    dummy_idx = torch.arange(n_samples, dtype=torch.long)
    return X, dummy_idx


# ---------------------------------------------------------------------------
# Ensemble Soft Label Computation
# ---------------------------------------------------------------------------

@torch.no_grad()
def compute_ensemble_soft_labels(
    client_models: List[nn.Module],
    X: torch.Tensor,
    temperature: float = 3.0,
    batch_size: int = 256,
    device: Optional[torch.device] = None,
    teacher_weights: Optional[Sequence[float]] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Compute ensemble soft labels for the attack classification head.

    FedDF Ensemble Labels (Lin et al. 2020):
        q_x = (1/K) Σ_{k=1}^{K} softmax(f_k(x) / T)

    We use the attack classification head (logits) for distillation,
    as it's the most informative head across diverse client distributions.

    Args:
        client_models: K client models {θ_1, ..., θ_K}.
        X:             Proxy input tensor [N, seq_len, n_features].
        temperature:   Softmax temperature T (higher → softer).
        batch_size:    Mini-batch size for forward passes.
        device:        Compute device.

    Returns:
        (attack_soft_labels, threat_soft_labels):
            attack_soft_labels: [N, num_attack_classes] — ensemble soft targets
            threat_soft_labels: [N, num_paths] — ensemble sigmoid threat scores
    """
    if device is None:
        device = next(client_models[0].parameters()).device

    if not client_models:
        raise ValueError("client_models cannot be empty")
    if teacher_weights is None:
        normalized_teacher_weights = np.full(len(client_models), 1.0 / len(client_models))
    else:
        raw_weights = np.asarray(teacher_weights, dtype=np.float64)
        if raw_weights.shape != (len(client_models),) or np.any(~np.isfinite(raw_weights)):
            raise ValueError("teacher_weights must contain one finite value per teacher")
        raw_weights = np.clip(raw_weights, 0.0, None)
        if float(raw_weights.sum()) <= 0.0:
            raise ValueError("at least one teacher weight must be positive")
        normalized_teacher_weights = raw_weights / raw_weights.sum()

    n_samples = len(X)
    loader = DataLoader(TensorDataset(X), batch_size=batch_size, shuffle=False)

    all_attack_logits: List[torch.Tensor] = []
    all_threat_scores: List[torch.Tensor] = []

    # Accumulate soft labels per batch
    for (xb,) in loader:
        xb = xb.to(device)
        batch_attack_soft = torch.zeros(len(xb), client_models[0].head_attack.out_features, device=device)
        batch_threat_soft = torch.zeros(len(xb), client_models[0].head_threat.out_features, device=device)

        for model, teacher_weight in zip(client_models, normalized_teacher_weights):
            model.eval()
            out = model(xb)
            # Attack: softmax with temperature
            attack_soft = F.softmax(out.attack_logits / temperature, dim=-1)
            batch_attack_soft = batch_attack_soft + float(teacher_weight) * attack_soft
            # Threat: sigmoid (already probabilistic)
            batch_threat_soft = batch_threat_soft + float(teacher_weight) * out.path_scores

        all_attack_logits.append(batch_attack_soft.cpu())
        all_threat_scores.append(batch_threat_soft.cpu())

    attack_soft = torch.cat(all_attack_logits, dim=0)
    threat_soft = torch.cat(all_threat_scores, dim=0)
    return attack_soft, threat_soft


@torch.no_grad()
def measure_teacher_disagreement(
    client_models: List[nn.Module],
    X: torch.Tensor,
    *,
    temperature: float = 3.0,
    device: Optional[torch.device] = None,
) -> float:
    """Mean predictive variance across teachers on the proxy population."""
    if len(client_models) < 2:
        return 0.0
    device = device or next(client_models[0].parameters()).device
    sample = X.to(device)
    predictions = []
    for model in client_models:
        model.eval()
        predictions.append(F.softmax(model(sample).attack_logits / temperature, dim=-1))
    return float(torch.stack(predictions).var(dim=0, unbiased=False).mean().cpu())


@torch.no_grad()
def _student_teacher_kl(
    global_model: nn.Module,
    X: torch.Tensor,
    attack_soft: torch.Tensor,
    temperature: float,
    device: torch.device,
) -> float:
    global_model.eval()
    output = global_model(X.to(device))
    log_soft = F.log_softmax(output.attack_logits / temperature, dim=-1)
    return float(
        F.kl_div(log_soft, attack_soft.to(device), reduction="batchmean").cpu()
        * (temperature ** 2)
    )


# ---------------------------------------------------------------------------
# FedDF Distillation Step
# ---------------------------------------------------------------------------

def feddf_distillation_step(
    global_model: nn.Module,
    client_models: List[nn.Module],
    proxy_X: torch.Tensor,
    temperature: float = 3.0,
    kd_epochs: int = 5,
    kd_lr: float = 0.001,
    alpha_kd: float = 0.7,
    batch_size: int = 128,
    device: Optional[torch.device] = None,
    teacher_weights: Optional[Sequence[float]] = None,
    min_teacher_disagreement: float = 0.0,
    max_proxy_kl_regression: float = 0.0,
    validation_loss_fn: Optional[Callable[[nn.Module], float]] = None,
    max_validation_loss_regression: float = 0.0,
) -> dict:
    """
    Full FedDF distillation step: distill ensemble into global model.

    FedDF Loss Function (Lin et al. 2020, Eq. 3):
        L_KD(θ) = α · (1/N) Σ_x KL(q_x || p_θ(x))
                + (1-α) · (1/N) Σ_x H(y_hard, p_θ(x))

    where:
        KL divergence:  KL(q || p) = Σ q_i log(q_i / p_i)
        p_θ(x) = softmax(f_θ(x) / T)   (student soft prediction)
        q_x = ensemble soft labels
        y_hard = argmax(q_x)            (pseudo hard labels from ensemble)

    Args:
        global_model:   Global model to update (student).
        client_models:  K client models for ensemble (teachers).
        proxy_X:        Unlabelled proxy dataset [N, T, F].
        temperature:    KD temperature T.
        kd_epochs:      Number of distillation training epochs.
        kd_lr:          Learning rate for distillation.
        alpha_kd:       KD loss weight (vs hard CE).
        batch_size:     Training batch size.
        device:         Compute device.

    Returns:
        Metrics dict: {kd_loss, ce_loss, total_loss, kd_improvement}.
    """
    if device is None:
        device = next(global_model.parameters()).device

    if not client_models:
        logger.warning("[KD] No client models provided — skipping distillation.")
        return {"applied": False, "skip_reason": "no_teachers", "kd_improvement": 0.0}

    K = len(client_models)
    logger.info("[KD] Starting FedDF distillation: K=%d teachers, T=%.1f, epochs=%d",
                K, temperature, kd_epochs)

    started_at = time.perf_counter()
    disagreement = measure_teacher_disagreement(
        client_models,
        proxy_X,
        temperature=temperature,
        device=device,
    )
    if disagreement < max(0.0, min_teacher_disagreement):
        return {
            "applied": False,
            "skip_reason": "teacher_disagreement_below_threshold",
            "teacher_disagreement": disagreement,
            "n_teachers": K,
            "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
        }

    # Step 1: Compute trust-weighted ensemble soft labels (no grad needed)
    proxy_X_dev = proxy_X.to(device)
    attack_soft, threat_soft = compute_ensemble_soft_labels(
        client_models,
        proxy_X_dev,
        temperature=temperature,
        batch_size=batch_size,
        device=device,
        teacher_weights=teacher_weights,
    )
    state_before = copy.deepcopy(global_model.state_dict())
    validation_loss_before = (
        float(validation_loss_fn(global_model)) if validation_loss_fn is not None else None
    )
    proxy_kl_before = _student_teacher_kl(
        global_model, proxy_X, attack_soft, temperature, device
    )
    # Hard labels from ensemble argmax (pseudo-labels)
    attack_hard = attack_soft.argmax(dim=-1).to(device)

    dataset = TensorDataset(proxy_X, attack_soft, attack_hard, threat_soft)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    # Step 2: Distill into global model
    global_model.train()
    optimizer = torch.optim.Adam(global_model.parameters(), lr=kd_lr)

    total_kd_loss = 0.0
    total_ce_loss = 0.0
    n_batches = 0

    for epoch in range(kd_epochs):
        epoch_kd = 0.0
        epoch_ce = 0.0
        for xb, atk_soft_b, atk_hard_b, thr_soft_b in loader:
            xb = xb.to(device)
            atk_soft_b = atk_soft_b.to(device)
            atk_hard_b = atk_hard_b.to(device)
            thr_soft_b = thr_soft_b.to(device)

            out = global_model(xb)

            # Student soft predictions
            student_log_soft = F.log_softmax(out.attack_logits / temperature, dim=-1)

            # KL divergence: KL(teacher || student) = Σ teacher · log(teacher/student)
            # Note: F.kl_div expects log-probabilities as input
            kd_loss = F.kl_div(
                student_log_soft,
                atk_soft_b,
                reduction="batchmean",
            ) * (temperature ** 2)  # scale by T² to normalize gradient magnitude

            # Hard CE loss (pseudo-labels from ensemble)
            ce_loss = F.cross_entropy(out.attack_logits, atk_hard_b)

            # Threat head: MSE against ensemble threat scores
            threat_loss = F.mse_loss(out.path_scores, thr_soft_b)

            total = alpha_kd * kd_loss + (1.0 - alpha_kd) * ce_loss + 0.1 * threat_loss

            optimizer.zero_grad()
            total.backward()
            optimizer.step()

            epoch_kd += kd_loss.item()
            epoch_ce += ce_loss.item()
            n_batches += 1

        logger.debug(
            "[KD] Epoch %d/%d: kd_loss=%.4f, ce_loss=%.4f",
            epoch + 1, kd_epochs, epoch_kd / len(loader), epoch_ce / len(loader),
        )
        total_kd_loss += epoch_kd
        total_ce_loss += epoch_ce

    n_batches = max(n_batches, 1)
    avg_kd = total_kd_loss / n_batches
    avg_ce = total_ce_loss / n_batches
    avg_total = alpha_kd * avg_kd + (1.0 - alpha_kd) * avg_ce

    proxy_kl_after = _student_teacher_kl(
        global_model, proxy_X, attack_soft, temperature, device
    )
    validation_loss_after = (
        float(validation_loss_fn(global_model)) if validation_loss_fn is not None else None
    )
    rollback_reason = None
    if proxy_kl_after > proxy_kl_before + max(0.0, max_proxy_kl_regression):
        rollback_reason = "proxy_kl_regressed"
    if (
        validation_loss_before is not None
        and validation_loss_after is not None
        and validation_loss_after
        > validation_loss_before + max(0.0, max_validation_loss_regression)
    ):
        rollback_reason = "validation_loss_regressed"
    if rollback_reason is not None:
        global_model.load_state_dict(state_before)

    logger.info(
        "[KD] Distillation complete: avg_kd_loss=%.4f, avg_ce_loss=%.4f",
        avg_kd, avg_ce,
    )

    return {
        "applied": rollback_reason is None,
        "rolled_back": rollback_reason is not None,
        "rollback_reason": rollback_reason,
        "kd_loss": round(avg_kd, 4),
        "ce_loss": round(avg_ce, 4),
        "total_loss": round(avg_total, 4),
        "n_teachers": K,
        "kd_epochs": kd_epochs,
        "temperature": temperature,
        "teacher_disagreement": round(disagreement, 8),
        "proxy_kl_before": round(proxy_kl_before, 6),
        "proxy_kl_after": round(proxy_kl_after, 6),
        "kd_improvement": round(proxy_kl_before - proxy_kl_after, 6),
        "validation_loss_before": validation_loss_before,
        "validation_loss_after": validation_loss_after,
        "trust_weighted_teachers": teacher_weights is not None,
        "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
    }


# ---------------------------------------------------------------------------
# Distillation Controller (used by server)
# ---------------------------------------------------------------------------

class DistillationController:
    """
    Manages when and how to run FedDF distillation each FL round.

    Call `maybe_distill()` each round — it internally tracks whether
    the configured interval has been reached.
    """

    def __init__(
        self,
        enabled: bool = True,
        temperature: float = 3.0,
        kd_epochs: int = 5,
        kd_every_n_rounds: int = 10,
        proxy_samples: int = 500,
        kd_lr: float = 0.001,
        alpha_kd: float = 0.7,
        seq_len: int = 10,
        n_features: int = 5,
        device: Optional[torch.device] = None,
        min_teacher_disagreement: float = 0.0,
        max_proxy_kl_regression: float = 0.0,
        max_validation_loss_regression: float = 0.0,
    ):
        self.enabled = enabled
        self.temperature = temperature
        self.kd_epochs = kd_epochs
        self.kd_every_n_rounds = kd_every_n_rounds if kd_every_n_rounds > 0 else 1
        self.proxy_samples = proxy_samples
        self.kd_lr = kd_lr
        self.alpha_kd = alpha_kd
        self.device = device or torch.device("cpu")
        self.min_teacher_disagreement = max(0.0, float(min_teacher_disagreement))
        self.max_proxy_kl_regression = max(0.0, float(max_proxy_kl_regression))
        self.max_validation_loss_regression = max(
            0.0, float(max_validation_loss_regression)
        )

        # Pre-generate proxy dataset once
        if enabled:
            self._proxy_X, _ = generate_proxy_dataset(proxy_samples, seq_len, n_features)
            logger.info(
                "[KD] Proxy dataset pre-generated: %d samples, seq_len=%d, features=%d",
                proxy_samples, seq_len, n_features,
            )
        else:
            self._proxy_X = None

        self._kd_history: List[dict] = []

    def should_run(self, round_num: int) -> bool:
        """Return True if distillation should run this round."""
        return self.enabled and (round_num % self.kd_every_n_rounds == 0)

    def maybe_distill(
        self,
        round_num: int,
        global_model: nn.Module,
        client_models: List[nn.Module],
        teacher_weights: Optional[Sequence[float]] = None,
        validation_loss_fn: Optional[Callable[[nn.Module], float]] = None,
    ) -> Optional[dict]:
        """
        Run distillation if the schedule and conditions are met.

        Args:
            round_num:     Current FL round.
            global_model:  The global model to update (in-place).
            client_models: Client teacher models for this round.

        Returns:
            Metrics dict if distillation ran, else None.
        """
        if not self.should_run(round_num):
            return None

        if len(client_models) < 2:
            logger.warning("[KD] Need ≥2 client models for FedDF. Skipping.")
            return None

        logger.info("[KD] Running FedDF distillation at round %d", round_num)
        metrics = feddf_distillation_step(
            global_model=global_model,
            client_models=client_models,
            proxy_X=self._proxy_X,
            temperature=self.temperature,
            kd_epochs=self.kd_epochs,
            kd_lr=self.kd_lr,
            alpha_kd=self.alpha_kd,
            device=self.device,
            teacher_weights=teacher_weights,
            min_teacher_disagreement=self.min_teacher_disagreement,
            max_proxy_kl_regression=self.max_proxy_kl_regression,
            validation_loss_fn=validation_loss_fn,
            max_validation_loss_regression=self.max_validation_loss_regression,
        )
        metrics["round"] = round_num
        metrics["proxy_environment_coverage"] = [
            "open_terrain",
            "urban",
            "far_from_jammer",
            "near_jammer",
        ]
        self._kd_history.append(metrics)
        return metrics

    def history(self) -> List[dict]:
        return self._kd_history


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_proxy_generation():
    """Verify proxy dataset has correct shape and value range."""
    X, idx = generate_proxy_dataset(n_samples=50, seq_len=10, n_features=5)
    assert X.shape == (50, 10, 5), f"Expected (50,10,5), got {X.shape}"
    assert (X >= 0.0).all() and (X <= 1.0).all(), "All features should be normalised to [0,1]"
    print(f"[PASS] Proxy generation: shape={X.shape}, range=[{X.min():.4f}, {X.max():.4f}]")


def _test_ensemble_labels():
    """Verify ensemble labels sum to 1 per sample."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from fl.model import build_model

    cfg = {"input_features": 5, "sequence_len": 10, "hidden_size": 16,
           "num_layers": 1, "dropout": 0.0, "num_paths": 3, "num_attack_classes": 5}
    models = [build_model(cfg) for _ in range(3)]

    X, _ = generate_proxy_dataset(n_samples=20, seq_len=10, n_features=5)
    attack_soft, threat_soft = compute_ensemble_soft_labels(models, X, temperature=3.0)

    assert attack_soft.shape == (20, 5), f"Expected (20,5), got {attack_soft.shape}"
    row_sums = attack_soft.sum(dim=-1)
    assert torch.allclose(row_sums, torch.ones_like(row_sums), atol=1e-5), "Soft labels must sum to 1"
    print(f"[PASS] Ensemble soft labels: shape={attack_soft.shape}, row_sum_range=[{row_sums.min():.4f},{row_sums.max():.4f}]")


def _test_distillation_step():
    """Verify distillation step runs without error and updates weights."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from fl.model import build_model, get_model_weights

    cfg = {"input_features": 5, "sequence_len": 10, "hidden_size": 16,
           "num_layers": 1, "dropout": 0.0, "num_paths": 3, "num_attack_classes": 5}

    global_model = build_model(cfg)
    client_models = [build_model(cfg) for _ in range(2)]
    weights_before = [w.copy() for w in get_model_weights(global_model)]

    X, _ = generate_proxy_dataset(n_samples=50, seq_len=10)
    metrics = feddf_distillation_step(
        global_model, client_models, X,
        temperature=3.0, kd_epochs=2, kd_lr=0.01, alpha_kd=0.7
    )

    weights_after = get_model_weights(global_model)
    weights_changed = any(
        not np.allclose(wb, wa)
        for wb, wa in zip(weights_before, weights_after)
    )
    assert weights_changed, "Distillation should update global model weights"
    assert "kd_loss" in metrics
    print(f"[PASS] Distillation step: kd_loss={metrics['kd_loss']:.4f}, weights_updated={weights_changed}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_proxy_generation()
    _test_ensemble_labels()
    _test_distillation_step()
    print("All fl/distillation.py tests passed.")
