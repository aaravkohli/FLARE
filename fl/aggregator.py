"""
fl/aggregator.py — [REAL]
Custom FL aggregation with 3-layer poisoning defense:
  Layer 1 — Gradient clipping (clip per-client weight delta by L2 norm)
  Layer 2 — Anomaly scoring  (Z-score across client updates, flag outliers)
  Layer 3 — Trimmed mean     (optional; drop top/bottom k% before averaging)

All layers are configurable via config/fl_config.yaml (security section).
"""

import logging
from typing import List, Optional, Tuple

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

    This limits the maximum influence any single client can have,
    reducing the impact of poisoned updates.
    """
    clipped = []
    for w, b in zip(weights, baseline):
        delta = w - b
        norm = np.linalg.norm(delta)
        if norm > max_norm:
            delta = delta * (max_norm / norm)
            logger.debug("Clipped weight delta: %.4f → %.4f", norm, max_norm)
        clipped.append(b + delta)
    return clipped


# ---------------------------------------------------------------------------
# Layer 2: Anomaly Scoring (Z-score)
# ---------------------------------------------------------------------------

def compute_anomaly_scores(
    all_weights: List[List[np.ndarray]],
) -> List[float]:
    """
    For each client, compute an anomaly score as the mean absolute Z-score
    of its flattened weight vector across the population.

    Returns a list of anomaly scores (one per client).
    Higher score = more anomalous.
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
            logger.warning("Client %d excluded (anomaly score=%.4f > %.4f)", i, score, z_threshold)
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

    More robust to Byzantine outliers than plain FedAvg.
    """
    if not all_weights:
        raise ValueError("No weights to aggregate.")

    n = len(all_weights)
    k = max(1, int(n * trim_ratio))  # number to drop from each tail

    aggregated = []
    for layer_idx in range(len(all_weights[0])):
        # Stack all clients' values for this layer: [n, *shape]
        stacked = np.stack([ws[layer_idx] for ws in all_weights], axis=0)
        orig_shape = stacked.shape[1:]

        flat = stacked.reshape(n, -1)  # [n, params]
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
    Falls back to uniform weighting if num_examples is None.
    """
    n = len(all_weights)
    if num_examples is None:
        num_examples = [1] * n
    total = sum(num_examples)

    aggregated = []
    for layer_idx in range(len(all_weights[0])):
        weighted = sum(
            (ne / total) * ws[layer_idx]
            for ws, ne in zip(all_weights, num_examples)
        )
        aggregated.append(weighted)
    return aggregated


# ---------------------------------------------------------------------------
# Main aggregation entry point
# ---------------------------------------------------------------------------

def secure_aggregate(
    client_weights: List[List[np.ndarray]],
    global_weights: List[np.ndarray],
    num_examples: Optional[List[int]] = None,
    clip_norm: float = 5.0,
    z_threshold: float = 2.5,
    use_trimmed_mean: bool = True,
    trim_ratio: float = 0.10,
) -> Tuple[List[np.ndarray], dict]:
    """
    Full secure aggregation pipeline:
      1. Clip each client's weight delta
      2. Score anomalies and remove outliers
      3. Aggregate with trimmed mean or FedAvg

    Returns:
      (aggregated_weights, audit_dict)
      audit_dict contains counts and excluded indices for logging.
    """
    n_clients = len(client_weights)
    logger.info("Aggregating %d client updates.", n_clients)

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
        return global_weights, {"excluded": excluded, "used": 0, "method": "none"}

    # Adjust num_examples to match valid clients
    if num_examples is not None:
        valid_ne = [ne for i, ne in enumerate(num_examples) if i not in excluded]
    else:
        valid_ne = None

    # Layer 3: aggregation
    if use_trimmed_mean and len(valid_weights) >= 3:
        aggregated = trimmed_mean(valid_weights, trim_ratio=trim_ratio)
        method = "trimmed_mean"
    else:
        aggregated = fedavg(valid_weights, valid_ne)
        method = "fedavg"

    audit = {
        "total_clients": n_clients,
        "excluded": excluded,
        "used": len(valid_weights),
        "anomaly_scores": scores,
        "method": method,
    }
    logger.info("Aggregation complete. Method=%s, Used=%d/%d, Excluded=%s",
                method, len(valid_weights), n_clients, excluded)
    return aggregated, audit
