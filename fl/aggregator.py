"""
fl/aggregator.py — Secure Aggregation Pipeline  [FLARE v2]

Secure aggregation primitives and the live Byzantine-resilient policy:
  - robust multi-signal analysis relative to the coordinate median
  - persisted historical trust, quarantine, and explicit rejection
  - robust sample-count caps and adaptive per-round delta clipping
  - trust-weighted FedAvg with coordinate-median/trimmed-mean fallback
  - optional server-side DP noise after aggregation

All policy parameters are configurable via config/fl_config.yaml. The legacy
``secure_aggregate`` entry point remains below for compatibility.

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
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from fl.trust import TrustManager, UpdateAnalysis, UpdateAnalyzer, robust_upper_bound

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

    aggregated: list[np.ndarray] = []
    for layer_idx in range(len(all_weights[0])):
        weighted: np.ndarray = sum(  # type: ignore[assignment]
            (ne / total) * ws[layer_idx]
            for ws, ne in zip(all_weights, num_examples)
        )
        aggregated.append(weighted)
    return aggregated


def coordinate_median(all_weights: List[List[np.ndarray]]) -> List[np.ndarray]:
    """Coordinate-wise median aggregation, robust below 50% Byzantine clients."""
    if not all_weights:
        raise ValueError("No weights to aggregate.")
    return [
        np.median(
            np.stack([client[layer_idx] for client in all_weights], axis=0),
            axis=0,
        ).astype(all_weights[0][layer_idx].dtype, copy=False)
        for layer_idx in range(len(all_weights[0]))
    ]


def _parameter_distance(
    first: List[np.ndarray], second: List[np.ndarray]
) -> float:
    """Return the L2 distance between two model parameter collections."""
    if len(first) != len(second):
        raise ValueError("Parameter collections must have the same structure")
    squared_distance = 0.0
    for left, right in zip(first, second):
        if left.shape != right.shape:
            raise ValueError("Parameter collections must have matching shapes")
        delta = np.asarray(left, dtype=np.float64) - np.asarray(right, dtype=np.float64)
        squared_distance += float(np.sum(delta * delta))
    return float(np.sqrt(squared_distance))


def run_shadow_protection_self_test(
    fl_config: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Verify Byzantine protection without touching the deployed federation.

    The test constructs small deterministic parameter *copies* in memory: four
    mutually consistent honest updates and one amplified sign-flip candidate.
    It then runs FLARE's real analyzer and aggregation policy with an ephemeral
    ``TrustManager``. No Flower client mode, model checkpoint, metrics snapshot,
    attack-control file, or persisted trust history is read or written.

    This is intentionally a protection self-test, not a live attack control.
    """
    config = dict(fl_config or {})
    trust_config = dict(config.get("trust", {}))
    byzantine_config = dict(config.get("byzantine_aggregation", {}))
    security_config = dict(config.get("security", {}))

    baseline = [
        np.zeros(6, dtype=np.float64),
        np.zeros(2, dtype=np.float64),
    ]
    reference_delta = [
        np.asarray([0.12, -0.08, 0.05, 0.09, -0.04, 0.07], dtype=np.float64),
        np.asarray([0.03, -0.02], dtype=np.float64),
    ]
    honest_scales = [0.94, 1.00, 1.06, 0.98]
    honest_updates = [
        [base + scale * delta for base, delta in zip(baseline, reference_delta)]
        for scale in honest_scales
    ]
    malicious_update = [
        base - 25.0 * delta for base, delta in zip(baseline, reference_delta)
    ]
    client_ids = [
        "reference_update_a",
        "reference_update_b",
        "reference_update_c",
        "reference_update_d",
        "synthetic_malicious_candidate",
    ]
    candidate_updates = honest_updates + [malicious_update]
    sample_counts = [100] * len(candidate_updates)

    analyzer = UpdateAnalyzer(
        robust_z_threshold=byzantine_config.get("robust_z_threshold", 3.5),
        suspicious_score=byzantine_config.get("suspicious_score", 0.35),
        malicious_score=byzantine_config.get("malicious_score", 0.75),
        min_clients=byzantine_config.get("min_clients_for_detection", 3),
        signal_weights=byzantine_config.get("signal_weights"),
        heterogeneity_tolerance=byzantine_config.get("heterogeneity_tolerance", 0.75),
        heterogeneous_population_threshold=byzantine_config.get(
            "heterogeneous_population_threshold", 0.35
        ),
        suspicious_min_evidence=byzantine_config.get("suspicious_min_evidence", 2),
        extreme_z_threshold=byzantine_config.get("extreme_z_threshold", 8.0),
        extreme_norm_ratio=byzantine_config.get("extreme_norm_ratio", 5.0),
        opposing_cosine_threshold=byzantine_config.get(
            "opposing_cosine_threshold", -0.10
        ),
    )
    # state_path=None is a deliberate invariant: self-test reputation exists
    # only for this function call and cannot alter live client trust history.
    trust_manager = TrustManager(
        history_alpha=trust_config.get("history_alpha", 0.70),
        initial_trust=trust_config.get("initial_trust", 0.80),
        suspicious_weight=trust_config.get("suspicious_weight", 0.25),
        reject_trust_threshold=trust_config.get("reject_trust_threshold", 0.15),
        suspicious_trust_threshold=trust_config.get(
            "suspicious_trust_threshold", 0.50
        ),
        quarantine_rounds=trust_config.get("quarantine_rounds", 3),
        quarantine_trigger_rounds=trust_config.get("quarantine_trigger_rounds", 2),
        history_size=trust_config.get("history_size", 20),
        state_path=None,
    )
    strategy = TrustWeightedAggregationStrategy(
        analyzer=analyzer,
        trust_manager=trust_manager,
        clip_norm=security_config.get("clip_norm", 5.0),
        suspicious_fraction_for_fallback=byzantine_config.get(
            "suspicious_fraction_for_fallback", 0.50
        ),
        fallback_strategy=byzantine_config.get(
            "fallback_strategy", "coordinate_median"
        ),
        trim_ratio=security_config.get("trim_ratio", 0.10),
        # Keep this diagnostic deterministic. Production DP configuration is
        # independent and remains active in the live Flower strategy.
        server_dp_noise_scale=0.0,
        adaptive_clip_enabled=byzantine_config.get("adaptive_clip_enabled", True),
        clip_mad_multiplier=byzantine_config.get("clip_mad_multiplier", 3.0),
        min_clip_norm=byzantine_config.get("min_clip_norm", 1e-6),
        max_sample_count_ratio=byzantine_config.get("max_sample_count_ratio", 3.0),
        sample_count_iqr_multiplier=byzantine_config.get(
            "sample_count_iqr_multiplier", 1.5
        ),
    )
    secure_aggregate, audit = strategy.aggregate(
        client_ids=client_ids,
        client_weights=candidate_updates,
        global_weights=baseline,
        num_examples=sample_counts,
    )
    honest_reference = fedavg(honest_updates, sample_counts[: len(honest_updates)])
    attacked_fedavg = fedavg(candidate_updates, sample_counts)
    attacked_distance = _parameter_distance(attacked_fedavg, honest_reference)
    secure_distance = _parameter_distance(secure_aggregate, honest_reference)
    malicious_evidence = next(
        row
        for row in audit["clients"]
        if row["client_id"] == "synthetic_malicious_candidate"
    )
    passed = bool(
        malicious_evidence["action"] == "REJECTED"
        and malicious_evidence["aggregation_weight"] == 0.0
        and secure_distance < attacked_distance
    )
    return {
        "passed": passed,
        "mode": "isolated_shadow_test",
        "protection_active": True,
        "global_model_modified": False,
        "trust_history_modified": False,
        "client_modes_modified": False,
        "aggregation_method": audit["method"],
        "malicious_candidate": malicious_evidence["client_id"],
        "malicious_candidate_action": malicious_evidence["action"],
        "attacked_fedavg_distance": round(attacked_distance, 6),
        "secure_aggregate_distance": round(secure_distance, 6),
        "damage_reduction_percent": round(
            100.0 * (1.0 - secure_distance / max(attacked_distance, 1e-12)), 3
        ),
        "rejected_updates": audit["rejected_updates"],
        "tested_updates": audit["total_clients"],
        "clients": audit["clients"],
    }


def detect_byzantine_updates(
    client_weights: List[List[np.ndarray]],
    global_weights: List[np.ndarray],
    client_ids: Optional[List[str]] = None,
    historical_norms: Optional[Dict[str, List[float]]] = None,
    **analyzer_config,
) -> List[dict]:
    """Public functional API for robust per-update Byzantine detection."""
    ids = client_ids or [f"client_{index}" for index in range(len(client_weights))]
    analyzer = UpdateAnalyzer(**analyzer_config)
    return [
        analysis.to_dict()
        for analysis in analyzer.analyze(ids, client_weights, global_weights, historical_norms)
    ]


class TrustWeightedAggregationStrategy:
    """Modular trust-weighted Byzantine-resilient aggregation policy.

    This class is independent of Flower transport types, which makes it usable
    from the live Flower strategy, unit tests, and offline experiments.
    """

    def __init__(
        self,
        analyzer: Optional[UpdateAnalyzer] = None,
        trust_manager: Optional[TrustManager] = None,
        clip_norm: float = 5.0,
        suspicious_fraction_for_fallback: float = 0.50,
        fallback_strategy: str = "coordinate_median",
        trim_ratio: float = 0.10,
        server_dp_noise_scale: float = 0.0,
        server_dp_sensitivity: Optional[float] = None,
        adaptive_clip_enabled: bool = True,
        clip_mad_multiplier: float = 3.0,
        min_clip_norm: float = 1e-6,
        max_sample_count_ratio: float = 3.0,
        sample_count_iqr_multiplier: float = 1.5,
    ):
        self.analyzer = analyzer or UpdateAnalyzer()
        self.trust_manager = trust_manager or TrustManager()
        self.clip_norm = float(clip_norm)
        self.suspicious_fraction_for_fallback = float(suspicious_fraction_for_fallback)
        self.fallback_strategy = fallback_strategy
        self.trim_ratio = float(trim_ratio)
        self.server_dp_noise_scale = float(server_dp_noise_scale)
        self.server_dp_sensitivity = float(server_dp_sensitivity or clip_norm)
        self.adaptive_clip_enabled = bool(adaptive_clip_enabled)
        self.clip_mad_multiplier = float(clip_mad_multiplier)
        self.min_clip_norm = float(min_clip_norm)
        self.max_sample_count_ratio = float(max_sample_count_ratio)
        self.sample_count_iqr_multiplier = float(sample_count_iqr_multiplier)
        if self.fallback_strategy not in {"coordinate_median", "trimmed_mean"}:
            raise ValueError("fallback_strategy must be coordinate_median or trimmed_mean")

    def aggregate(
        self,
        client_ids: List[str],
        client_weights: List[List[np.ndarray]],
        global_weights: List[np.ndarray],
        num_examples: Optional[List[int]] = None,
        forced_rejections: Optional[Dict[int, str]] = None,
    ) -> Tuple[List[np.ndarray], dict]:
        if not client_weights:
            raise ValueError("No client weights to aggregate")
        if len(client_ids) != len(client_weights):
            raise ValueError("client_ids and client_weights must have equal length")
        if num_examples is None:
            num_examples = [1] * len(client_weights)
        if len(num_examples) != len(client_weights):
            raise ValueError("num_examples and client_weights must have equal length")

        rejection_reasons = dict(forced_rejections or {})
        validated_num_examples: List[int] = []
        reported_num_examples: List[int] = []
        for index, value in enumerate(num_examples):
            valid_count = isinstance(value, (int, np.integer)) and not isinstance(
                value, (bool, np.bool_)
            )
            reported_num_examples.append(int(value) if valid_count else 0)
            if valid_count and int(value) > 0:
                validated_num_examples.append(int(value))
                continue
            validated_num_examples.append(0)
            count_reason = f"invalid non-positive sample count: {value!r}"
            prior_reason = rejection_reasons.get(index)
            rejection_reasons[index] = (
                f"{prior_reason}; {count_reason}" if prior_reason else count_reason
            )
        num_examples = validated_num_examples

        analyses = self.analyzer.analyze(
            client_ids,
            client_weights,
            global_weights,
            self.trust_manager.historical_norms(),
        )
        for index, reason in rejection_reasons.items():
            if not 0 <= index < len(analyses):
                raise ValueError("forced rejection index is outside the client result list")
            analysis = analyses[index]
            analysis.status = "MALICIOUS"
            analysis.deviation_score = 1.0
            analysis.evidence_count = max(analysis.evidence_count, 4)
            analysis.rejection_reason = f"identity violation: {reason}"
        analyses = self.trust_manager.update_round(analyses)

        positive_counts = np.asarray(
            [float(value) for value in num_examples if value > 0], dtype=np.float64
        )
        if positive_counts.size:
            count_median = float(np.median(positive_counts))
            q1, q3 = np.percentile(positive_counts, [25.0, 75.0])
            iqr_cap = float(q3 + self.sample_count_iqr_multiplier * (q3 - q1))
            ratio_cap = count_median * max(1.0, self.max_sample_count_ratio)
            sample_count_cap = max(count_median, min(iqr_cap, ratio_cap))
        else:
            sample_count_cap = 1.0
        effective_num_examples = [
            min(float(value), sample_count_cap) if value > 0 else 0.0
            for value in num_examples
        ]
        for analysis, reported, effective in zip(
            analyses, reported_num_examples, effective_num_examples
        ):
            analysis.reported_num_examples = int(reported)
            analysis.effective_num_examples = effective

        included_indices = [
            index for index, analysis in enumerate(analyses)
            if analysis.action != "REJECTED"
        ]
        suspicious_count = sum(analysis.status != "NORMAL" for analysis in analyses)
        suspicious_fraction = suspicious_count / len(analyses)

        if not included_indices:
            audit = self._audit(
                analyses,
                method="retain_global",
                suspicious_count=suspicious_count,
                used=0,
                dp_applied=False,
                sample_count_cap=sample_count_cap,
            )
            logger.error("All client updates rejected; retaining the current global model")
            return [weight.copy() for weight in global_weights], audit

        included_norms = [analyses[index].update_norm for index in included_indices]
        effective_clip_norm = self.clip_norm
        if self.adaptive_clip_enabled and included_norms:
            adaptive_bound = robust_upper_bound(included_norms, self.clip_mad_multiplier)
            effective_clip_norm = min(
                self.clip_norm,
                max(self.min_clip_norm, adaptive_bound),
            )

        clipped_weights = [
            clip_weights_by_norm(client_weights[index], global_weights, effective_clip_norm)
            for index in included_indices
        ]
        use_fallback = (
            suspicious_fraction >= self.suspicious_fraction_for_fallback
            and len(clipped_weights) >= 2
        )

        if use_fallback and self.fallback_strategy == "coordinate_median":
            aggregated = coordinate_median(clipped_weights)
            method = "coordinate_median_fallback"
            fallback_weight = 1.0 / len(included_indices)
            for index in included_indices:
                analyses[index].aggregation_weight = fallback_weight
        elif use_fallback:
            aggregated = trimmed_mean(clipped_weights, self.trim_ratio)
            method = "trimmed_mean_fallback"
            fallback_weight = 1.0 / len(included_indices)
            for index in included_indices:
                analyses[index].aggregation_weight = fallback_weight
        else:
            raw_weights = []
            for index in included_indices:
                analysis = analyses[index]
                action_multiplier = (
                    self.trust_manager.suspicious_weight
                    if analysis.action == "DOWN_WEIGHTED"
                    else 1.0
                )
                raw_weights.append(
                    effective_num_examples[index]
                    * analysis.trust_score
                    * action_multiplier
                )
            raw_total = sum(raw_weights)
            if raw_total <= 1e-12:
                normalized = [1.0 / len(raw_weights)] * len(raw_weights)
            else:
                normalized = [weight / raw_total for weight in raw_weights]
            aggregated = trust_weighted_aggregate(clipped_weights, normalized)
            method = "trust_weighted_fedavg"
            for index, weight in zip(included_indices, normalized):
                analyses[index].aggregation_weight = weight

        dp_applied = self.server_dp_noise_scale > 0.0
        if dp_applied:
            aggregated = apply_server_dp_noise(
                aggregated,
                noise_scale=self.server_dp_noise_scale,
                sensitivity=self.server_dp_sensitivity,
            )

        audit = self._audit(
            analyses,
            method=method,
            suspicious_count=suspicious_count,
            used=len(included_indices),
            dp_applied=dp_applied,
            effective_clip_norm=effective_clip_norm,
            sample_count_cap=sample_count_cap,
        )
        logger.info(
            "Byzantine aggregation: method=%s used=%d/%d suspicious=%d rejected=%d",
            method,
            len(included_indices),
            len(analyses),
            suspicious_count,
            len(audit["excluded"]),
        )
        for client in audit["clients"]:
            logger.info(
                "  %s trust=%.3f deviation=%.3f norm=%.3f %s",
                client["client_id"],
                client["trust_score"],
                client["deviation_score"],
                client["update_norm"],
                client["action"],
            )
        return aggregated, audit

    def _audit(
        self,
        analyses: List[UpdateAnalysis],
        method: str,
        suspicious_count: int,
        used: int,
        dp_applied: bool,
        effective_clip_norm: Optional[float] = None,
        sample_count_cap: Optional[float] = None,
    ) -> dict:
        trust_summary = self.trust_manager.summary()
        return {
            "total_clients": len(analyses),
            "used": used,
            "excluded": [
                index for index, analysis in enumerate(analyses)
                if analysis.action == "REJECTED"
            ],
            "method": method,
            "aggregation_strategy": method,
            "suspected_malicious_clients": suspicious_count,
            "rejected_updates": sum(analysis.action == "REJECTED" for analysis in analyses),
            "down_weighted_updates": sum(analysis.action == "DOWN_WEIGHTED" for analysis in analyses),
            "server_dp_applied": dp_applied,
            "server_dp_noise_scale": self.server_dp_noise_scale,
            "configured_clip_norm": self.clip_norm,
            "effective_clip_norm": round(
                float(effective_clip_norm if effective_clip_norm is not None else self.clip_norm), 6
            ),
            "sample_count_cap": round(float(sample_count_cap or 0.0), 6),
            "sample_count_capped_clients": sum(
                analysis.effective_num_examples < analysis.reported_num_examples
                for analysis in analyses
            ),
            "clients": [analysis.to_dict() for analysis in analyses],
            **trust_summary,
        }


# ---------------------------------------------------------------------------
# Layer 4: Trust-Weighted Aggregation  [NEW in v2]
# ---------------------------------------------------------------------------

def trust_weighted_aggregate(
    all_weights: List[List[np.ndarray]],
    agg_weights: List[float],
) -> List[np.ndarray]:
    """
    Aggregate client weights using precomputed trust-based weights.

    FLARE trust-weighted FedAvg:
        θ = Σ_i (τ_i · n_i / Σ_j τ_j · n_j) · θ_i

    The production weights are computed from server-side ``TrustManager``
    evidence and robustly capped sample counts. This is not the FLTrust root
    dataset algorithm.

    Args:
        all_weights:  Client model weights.
        agg_weights:  Normalized trust-weighted aggregation weights.

    Returns:
        Aggregated global model weights.
    """
    if abs(sum(agg_weights) - 1.0) > 1e-5:
        total = max(sum(agg_weights), 1e-10)
        agg_weights = [w / total for w in agg_weights]

    aggregated: list[np.ndarray] = []
    for layer_idx in range(len(all_weights[0])):
        weighted: np.ndarray = sum(  # type: ignore[assignment]
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
