"""
fl/aggregator.py — Secure Aggregation Pipeline  [FLARE v2]

Upgraded 5-layer secure aggregation pipeline:
  Layer 1 — Gradient Clipping       : clip per-client weight delta by L2 norm
  Layer 2 — Anomaly Scoring          : Z-score across client updates, flag outliers
  Layer 3 — Trimmed Mean             : drop top/bottom k% before averaging
  Layer 4 — Trust-Weighted Averaging : weight clients by reputation score τ_i
  Layer 5 — Server-Side DP Noise     : add calibrated Gaussian noise post-aggregation

All layers are individually configurable via config/fl_config.yaml.
Layers 4 and 5 are NEW in FLARE v2.

References:
  FedAvg:     McMahan et al. (2017). Communication-Efficient Learning of Deep
              Networks from Decentralized Data. AISTATS 2017.
  Trimmed Mean: Yin et al. (2018). Byzantine-Robust Distributed Learning:
              Towards Optimal Statistical Rates. ICML 2018.
  DP-Noise:   Geyer et al. (2017). Differentially Private Federated Learning:
              A Client Level Perspective. https://arxiv.org/abs/1712.07557
  FLTrust:    Cao et al. (2020). FLTrust: Byzantine-robust FL via Trust
              Bootstrapping. NDSS 2021.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 1: Gradient Clipping
# ---------------------------------------------------------------------------

def clip_weights_by_norm(
    weights: List[np.ndarray],
    baseline: List[np.ndarray],
    max_norm: float = 5.0,
) -> List[np.ndarray]:
    """
    Clip the weight *delta* (weights - baseline) to max_norm in L2 norm.
    Returns the clipped weights (baseline + clipped_delta).

    DP-SGD Clipping (Abadi 2016):
        Δw = w - θ
        Δw̃ = Δw / max(1, ||Δw||₂ / C)
        w̃ = θ + Δw̃

    This limits the maximum influence any single client can have,
    reducing the impact of poisoned updates and bounding global sensitivity.
    """
    if len(weights) != len(baseline):
        raise ValueError("Client and baseline weight lists must have the same length")
    if max_norm <= 0:
        raise ValueError("max_norm must be positive")

    deltas = [w - b for w, b in zip(weights, baseline)]
    global_norm = float(np.sqrt(sum(
        np.sum(delta.astype(np.float64) ** 2)
        for delta in deltas
    )))
    scale = min(1.0, max_norm / max(global_norm, 1e-12))
    if scale < 1.0:
        logger.debug("Clipped global client delta: %.4f → %.4f", global_norm, max_norm)
    return [b + delta * scale for delta, b in zip(deltas, baseline)]


# ---------------------------------------------------------------------------
# Layer 2: Anomaly Scoring (Z-score)
# ---------------------------------------------------------------------------

def compute_anomaly_scores(
    all_weights: List[List[np.ndarray]],
) -> List[float]:
    """
    For each client, compute an anomaly score as the mean absolute Z-score
    of its flattened weight vector across the population.

    Z-score Anomaly Detection:
        μ = mean(W)         [per parameter]
        σ = std(W) + ε
        z_i = |W_i - μ| / σ
        score_i = mean(z_i)

    Higher score = more anomalous = potentially poisoned.

    Returns a list of anomaly scores (one per client).
    """
    flat = [np.concatenate([w.flatten() for w in ws]) for ws in all_weights]
    flat_arr = np.stack(flat, axis=0)   # [num_clients, total_params]

    mean = flat_arr.mean(axis=0)
    std = flat_arr.std(axis=0) + 1e-8   # avoid division by zero

    scores = []
    for row in flat_arr:
        z = np.abs((row - mean) / std)
        scores.append(float(z.mean()))
    return scores


def filter_by_anomaly_score(
    all_weights: List[List[np.ndarray]],
    scores: List[float],
    z_threshold: float = 2.5,
) -> Tuple[List[List[np.ndarray]], List[int]]:
    """
    Remove clients whose anomaly score exceeds z_threshold.
    Returns (valid_weights, excluded_indices).
    """
    valid, excluded = [], []
    for i, (ws, score) in enumerate(zip(all_weights, scores)):
        if score > z_threshold:
            logger.warning(
                "Client %d excluded (anomaly score=%.4f > %.4f)", i, score, z_threshold
            )
            excluded.append(i)
        else:
            valid.append(ws)
    return valid, excluded


# ---------------------------------------------------------------------------
# Layer 3: Trimmed Mean Aggregation
# ---------------------------------------------------------------------------

def trimmed_mean(
    all_weights: List[List[np.ndarray]],
    trim_ratio: float = 0.10,
) -> List[np.ndarray]:
    """
    Per-parameter trimmed mean: sort client values for each weight element,
    drop the top and bottom trim_ratio fraction, then average.

    Trimmed Mean (Yin et al. 2018):
        Sort w^(1) ≤ w^(2) ≤ ... ≤ w^(n) per parameter
        Trimmed mean = mean(w^(k+1), ..., w^(n-k))
        where k = floor(n × trim_ratio)

    More robust to Byzantine outliers than plain FedAvg.
    """
    if not all_weights:
        raise ValueError("No weights to aggregate.")

    n = len(all_weights)
    if not 0.0 <= trim_ratio < 0.5:
        raise ValueError("trim_ratio must be in [0, 0.5)")
    k = int(n * trim_ratio)  # number to drop from each tail

    if k == 0:
        return fedavg(all_weights)

    aggregated = []
    for layer_idx in range(len(all_weights[0])):
        stacked = np.stack([ws[layer_idx] for ws in all_weights], axis=0)
        orig_shape = stacked.shape[1:]
        flat = stacked.reshape(n, -1)   # [n, params]
        flat_sorted = np.sort(flat, axis=0)
        trimmed = flat_sorted[k : n - k]  # remove k from each tail
        aggregated.append(trimmed.mean(axis=0).reshape(orig_shape))
    return aggregated


# ---------------------------------------------------------------------------
# Plain FedAvg (weighted average by num_examples)
# ---------------------------------------------------------------------------

def fedavg(
    all_weights: List[List[np.ndarray]],
    num_examples: Optional[List[int]] = None,
) -> List[np.ndarray]:
    """
    Standard FedAvg weighted by number of training examples.

    FedAvg (McMahan et al. 2017):
        θ_{t+1} = Σ_i (n_i / n) · θ_i
        where n = Σ_i n_i

    Falls back to uniform weighting if num_examples is None.
    """
    n = len(all_weights)
    if num_examples is None:
        num_examples = [1] * n
    total = max(sum(num_examples), 1)

    aggregated = []
    for layer_idx in range(len(all_weights[0])):
        weighted = sum(
            (ne / total) * ws[layer_idx]
            for ws, ne in zip(all_weights, num_examples)
        )
        aggregated.append(weighted)
    return aggregated


# ---------------------------------------------------------------------------
# Layer 4: Trust-Weighted Aggregation  [NEW in v2]
# ---------------------------------------------------------------------------

def trust_weighted_aggregate(
    all_weights: List[List[np.ndarray]],
    agg_weights: List[float],
) -> List[np.ndarray]:
    """
    Aggregate client weights using precomputed trust-based weights.

    Trust-Weighted FedAvg (Cao et al. 2020, FLTrust):
        θ = Σ_i (τ_i · n_i / Σ_j τ_j · n_j) · θ_i

    The aggregation weights are computed externally by TrustRegistry
    (fl.trust.TrustRegistry.get_aggregation_weights).

    Args:
        all_weights:  Client model weights.
        agg_weights:  Normalized trust-weighted aggregation weights.

    Returns:
        Aggregated global model weights.
    """
    if abs(sum(agg_weights) - 1.0) > 1e-5:
        total = max(sum(agg_weights), 1e-10)
        agg_weights = [w / total for w in agg_weights]

    aggregated = []
    for layer_idx in range(len(all_weights[0])):
        weighted = sum(
            w * ws[layer_idx]
            for w, ws in zip(agg_weights, all_weights)
        )
        aggregated.append(weighted)

    logger.debug(
        "Trust-weighted aggregation: %d clients, weights=[%s]",
        len(all_weights),
        ", ".join(f"{w:.4f}" for w in agg_weights),
    )
    return aggregated


# ---------------------------------------------------------------------------
# Layer 5: Server-Side DP Noise  [NEW in v2]
# ---------------------------------------------------------------------------

def apply_server_dp_noise(
    weights: List[np.ndarray],
    noise_scale: float,
    sensitivity: float,
) -> List[np.ndarray]:
    """
    Apply server-side Gaussian DP noise to aggregated weights.

    Server DP Mechanism (Geyer et al. 2017):
        θ_noised = θ + N(0, (noise_scale · sensitivity)² · I)

    This provides additional privacy protection on top of client-side DP.

    Args:
        weights:     Aggregated global model weights.
        noise_scale: σ_server (from config: differential_privacy.server_side.noise_scale).
        sensitivity: Δf (from config: security.clip_norm — bounds the update).

    Returns:
        Noised global weights.
    """
    from fl.privacy import add_server_side_dp_noise
    return add_server_side_dp_noise(weights, noise_scale=noise_scale, sensitivity=sensitivity)


# ---------------------------------------------------------------------------
# Main aggregation entry point  [UPGRADED v2]
# ---------------------------------------------------------------------------

def secure_aggregate(
    client_weights: List[List[np.ndarray]],
    global_weights: List[np.ndarray],
    num_examples: Optional[List[int]] = None,
    clip_norm: float = 5.0,
    z_threshold: float = 2.5,
    use_trimmed_mean: bool = True,
    trim_ratio: float = 0.10,
    # NEW v2 parameters
    agg_weights: Optional[List[float]] = None,
    server_dp_noise_scale: float = 0.0,
    server_dp_sensitivity: float = 5.0,
) -> Tuple[List[np.ndarray], dict]:
    """
    Full secure aggregation pipeline (5 layers):

      Layer 1: Clip each client's weight delta to clip_norm (L2)
      Layer 2: Score anomalies (Z-score) and remove outliers
      Layer 3: Aggregate with trimmed mean or trust-weighted FedAvg
      Layer 4: If agg_weights provided → trust-weighted average
      Layer 5: If server_dp_noise_scale > 0 → add Gaussian DP noise

    Backward compatible: new parameters default to v1 behavior when omitted.

    Args:
        client_weights:          Per-client model weights.
        global_weights:          Current global model (clipping baseline).
        num_examples:            Training samples per client (for FedAvg weighting).
        clip_norm:               Layer 1 L2 clip norm.
        z_threshold:             Layer 2 Z-score exclusion threshold.
        use_trimmed_mean:        Layer 3 trimmed mean vs FedAvg.
        trim_ratio:              Layer 3 trim fraction.
        agg_weights:             Layer 4 trust aggregation weights (or None → skip).
        server_dp_noise_scale:   Layer 5 DP noise scale (0.0 → skip).
        server_dp_sensitivity:   Layer 5 sensitivity bound.

    Returns:
        (aggregated_weights, audit_dict)
    """
    n_clients = len(client_weights)
    logger.info("Aggregating %d client updates (v2 pipeline).", n_clients)

    # Layer 1: gradient clipping
    clipped = [
        clip_weights_by_norm(ws, global_weights, max_norm=clip_norm)
        for ws in client_weights
    ]

    # Layer 2: anomaly scoring
    scores = compute_anomaly_scores(clipped)
    valid_weights, excluded = filter_by_anomaly_score(clipped, scores, z_threshold)

    if len(valid_weights) == 0:
        logger.error("All clients excluded as anomalous — returning global weights unchanged.")
        return global_weights, {
            "excluded": excluded, "used": 0, "method": "none",
            "total_clients": n_clients, "anomaly_scores": scores,
        }

    # Adjust num_examples and agg_weights to match valid clients
    valid_ne: Optional[List[int]] = None
    valid_agg_weights: Optional[List[float]] = None
    if num_examples is not None:
        valid_ne = [ne for i, ne in enumerate(num_examples) if i not in excluded]
    if agg_weights is not None:
        valid_agg_weights = [w for i, w in enumerate(agg_weights) if i not in excluded]

    # Layer 3 + 4: aggregation
    if valid_agg_weights is not None and sum(valid_agg_weights) > 1e-10:
        # Layer 4: trust-weighted aggregation (overrides trimmed mean)
        aggregated = trust_weighted_aggregate(valid_weights, valid_agg_weights)
        method = "trust_weighted"
    elif use_trimmed_mean and len(valid_weights) >= 3:
        # Layer 3: trimmed mean
        aggregated = trimmed_mean(valid_weights, trim_ratio=trim_ratio)
        method = "trimmed_mean"
    else:
        # Fallback: FedAvg
        aggregated = fedavg(valid_weights, valid_ne)
        method = "fedavg"

    # Layer 5: server-side DP noise
    dp_applied = False
    if server_dp_noise_scale > 0.0:
        aggregated = apply_server_dp_noise(
            aggregated,
            noise_scale=server_dp_noise_scale,
            sensitivity=server_dp_sensitivity,
        )
        dp_applied = True

    audit = {
        "total_clients": n_clients,
        "excluded": excluded,
        "used": len(valid_weights),
        "anomaly_scores": scores,
        "method": method,
        "server_dp_applied": dp_applied,
        "server_dp_noise_scale": server_dp_noise_scale,
        "trust_weighted": valid_agg_weights is not None,
        "agg_weights_used": [round(w, 4) for w in (valid_agg_weights or [])],
    }
    logger.info(
        "Aggregation complete: method=%s, used=%d/%d, excluded=%s, dp=%s",
        method, len(valid_weights), n_clients, excluded, dp_applied,
    )
    return aggregated, audit
