"""
fl/drift.py — Multi-Signal Client Drift Detection  [FLARE v2]

Detects three types of client drift that degrade FL convergence:

  Signal 1 — Gradient Direction Drift (Cosine Similarity)
    Measures angular shift between consecutive gradient directions.
    Low cosine similarity → client objective shifted (concept drift or attack).

  Signal 2 — Data Distribution Drift (KS Test on RSSI)
    Kolmogorov-Smirnov test on the RSSI distribution vs. baseline.
    Existing in client.py, unified and server-trackable here.

  Signal 3 — Loss Spike Detection
    Ratio of current to previous round loss. Sharp spikes indicate
    data distribution shift, poisoning, or adversarial fine-tuning.

Combined Drift Score:
    drift_score_i = w1 · (1 - cos_sim_i) + w2 · KS_stat_i + w3 · loss_ratio_i
    drift_detected if drift_score_i > combined_threshold

Reference:
  Karimireddy, S.P. et al. (2020). SCAFFOLD: Stochastic Controlled Averaging
  for Federated Learning. ICML 2020. https://arxiv.org/abs/1910.06378

  Concept Drift in Federated Learning:
  Pan, S.J. & Yang, Q. (2010). A Survey on Transfer Learning.
  IEEE Transactions on Knowledge and Data Engineering, 22(10).

Computational overhead:
  Cosine similarity: O(d) — negligible
  KS test:           O(n log n) — negligible for RSSI windows
  Loss ratio:        O(1) — trivial

Unit-test hooks: run `python -m fl.drift` for self-test.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import ks_2samp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-Client Drift State
# ---------------------------------------------------------------------------

class ClientDriftState:
    """
    Tracks drift-related history for a single client.

    Attributes:
        prev_grad_flat:   Flattened gradient from previous round (for cosine sim).
        rssi_baseline:    RSSI distribution baseline for KS test.
        loss_history:     Recent per-round losses (bounded deque).
        drift_scores:     Recent combined drift scores.
        drift_count:      Total drift events detected for this client.
    """

    def __init__(self, client_id: str, loss_window: int = 5):
        self.client_id = client_id
        self.prev_grad_flat: Optional[np.ndarray] = None
        self.rssi_baseline: Optional[np.ndarray] = None
        self.loss_history: deque = deque(maxlen=loss_window)
        self.drift_scores: deque = deque(maxlen=10)
        self.drift_count: int = 0

    def update_grad(self, weights: List[np.ndarray]) -> None:
        """Store flattened weight delta as proxy for gradient direction."""
        self.prev_grad_flat = np.concatenate([w.flatten() for w in weights])

    def update_loss(self, loss: float) -> None:
        self.loss_history.append(loss)

    def update_rssi(self, rssi: np.ndarray) -> None:
        if self.rssi_baseline is None:
            self.rssi_baseline = rssi.copy()


# ---------------------------------------------------------------------------
# Individual Signal Detectors
# ---------------------------------------------------------------------------

def cosine_similarity_drift(
    prev_grad: Optional[np.ndarray],
    curr_grad: np.ndarray,
    threshold: float = 0.3,
) -> Tuple[float, bool]:
    """
    Detect gradient direction drift using cosine similarity.

    Gradient Cosine Similarity:
        cos(g_t, g_{t-1}) = (g_t · g_{t-1}) / (||g_t||₂ · ||g_{t-1}||₂)

    Drift detected when:
        cos(g_t, g_{t-1}) < threshold

    Low cosine similarity indicates the client's optimization direction
    has changed significantly — possible concept drift or data poisoning.

    Reference:
        Karimireddy et al. 2020 (SCAFFOLD) — defines client drift as
        deviation of local gradients from the global gradient direction.

    Args:
        prev_grad:  Gradient from previous round (None → no detection).
        curr_grad:  Gradient from current round.
        threshold:  Cosine similarity below which drift is declared.

    Returns:
        (cosine_sim, drift_detected)
    """
    if prev_grad is None or prev_grad.shape != curr_grad.shape:
        return 1.0, False

    norm_prev = np.linalg.norm(prev_grad)
    norm_curr = np.linalg.norm(curr_grad)

    if norm_prev < 1e-10 or norm_curr < 1e-10:
        return 1.0, False

    cos_sim = float(np.dot(prev_grad, curr_grad) / (norm_prev * norm_curr))
    cos_sim = max(-1.0, min(1.0, cos_sim))  # numerical safety

    drift = cos_sim < threshold
    if drift:
        logger.debug(
            "Cosine drift: cos_sim=%.4f < threshold=%.4f",
            cos_sim, threshold,
        )
    return cos_sim, drift


def ks_test_drift(
    baseline_rssi: Optional[np.ndarray],
    current_rssi: np.ndarray,
    p_threshold: float = 0.05,
) -> Tuple[float, float, bool]:
    """
    KS test for RSSI distribution drift.

    Kolmogorov-Smirnov Test:
        D_n = sup_x |F_1(x) - F_2(x)|
        Reject H0 (same distribution) if p < p_threshold

    Args:
        baseline_rssi: RSSI values from the initial reference round.
        current_rssi:  Current round RSSI values.
        p_threshold:   P-value threshold (typically 0.05).

    Returns:
        (ks_statistic, p_value, drift_detected)
    """
    if baseline_rssi is None or len(baseline_rssi) < 5 or len(current_rssi) < 5:
        return 0.0, 1.0, False

    ks_stat, p_value = ks_2samp(baseline_rssi, current_rssi)
    drift = p_value < p_threshold
    if drift:
        logger.debug(
            "KS drift: D=%.4f, p=%.4f < %.4f",
            ks_stat, p_value, p_threshold,
        )
    return float(ks_stat), float(p_value), drift


def loss_spike_drift(
    loss_history: deque,
    ratio_threshold: float = 2.0,
) -> Tuple[float, bool]:
    """
    Detect loss spikes indicating sudden data distribution shift.

    Loss Spike Criterion:
        loss_ratio = L_t / L_{t-1}
        Drift detected when loss_ratio > ratio_threshold

    A sudden increase in loss can indicate:
      - Concept drift (new attack pattern appears)
      - Data poisoning (adversarial fine-tuning between rounds)
      - Model-replacement attack (large weight update)

    Args:
        loss_history: Deque of recent losses (most recent last).
        ratio_threshold: Ratio above which drift is declared.

    Returns:
        (loss_ratio, drift_detected)
    """
    if len(loss_history) < 2:
        return 1.0, False

    losses = list(loss_history)
    prev_loss = losses[-2]
    curr_loss = losses[-1]

    if prev_loss < 1e-10:
        return 1.0, False

    ratio = curr_loss / prev_loss
    drift = ratio > ratio_threshold
    if drift:
        logger.debug(
            "Loss spike drift: L_t/L_{t-1} = %.4f > %.4f",
            ratio, ratio_threshold,
        )
    return float(ratio), drift


# ---------------------------------------------------------------------------
# Drift Registry (Server-Side)
# ---------------------------------------------------------------------------

class DriftRegistry:
    """
    Server-side tracker for multi-signal client drift detection.

    Call `update()` each round with client gradient vectors and metrics.
    Receives combined drift scores and audit logs per client.
    """

    def __init__(
        self,
        cosine_threshold: float = 0.3,
        ks_p_threshold: float = 0.05,
        loss_spike_threshold: float = 2.0,
        weights: Optional[dict] = None,
        combined_threshold: float = 0.5,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.cosine_threshold = cosine_threshold
        self.ks_p_threshold = ks_p_threshold
        self.loss_spike_threshold = loss_spike_threshold
        self.combined_threshold = combined_threshold

        # Signal weights for combined drift score
        default_weights = {"cosine": 0.4, "ks": 0.3, "loss_spike": 0.3}
        self.weights = weights or default_weights

        self._states: Dict[str, ClientDriftState] = {}

    def _get_state(self, client_id: str) -> ClientDriftState:
        if client_id not in self._states:
            self._states[client_id] = ClientDriftState(client_id)
        return self._states[client_id]

    def update(
        self,
        client_id: str,
        curr_weights: List[np.ndarray],
        train_loss: Optional[float] = None,
        rssi_values: Optional[np.ndarray] = None,
    ) -> dict:
        """
        Run all drift signals for a client after a round.

        Combined Drift Score:
            drift_score = w_cos·(1 - cos_sim) + w_ks·KS_stat + w_spike·loss_ratio_norm

        where loss_ratio_norm = clip((loss_ratio - 1) / (threshold - 1), 0, 1)

        Args:
            client_id:    Client identifier.
            curr_weights: Client's model weights after this round.
            train_loss:   Training loss reported by client.
            rssi_values:  RSSI observations (for KS test).

        Returns:
            Drift audit dict with all signal values and combined score.
        """
        if not self.enabled:
            return {"drift_detected": False, "drift_score": 0.0, "client_id": client_id}

        state = self._get_state(client_id)
        curr_grad_flat = np.concatenate([w.flatten() for w in curr_weights])

        # Signal 1: Cosine similarity
        cos_sim, cos_drift = cosine_similarity_drift(
            state.prev_grad_flat, curr_grad_flat, self.cosine_threshold
        )

        # Signal 2: KS test on RSSI
        if rssi_values is not None and state.rssi_baseline is None:
            state.rssi_baseline = rssi_values.copy()
        ks_stat, ks_pval, ks_drift = ks_test_drift(
            state.rssi_baseline, rssi_values if rssi_values is not None else np.array([]),
            self.ks_p_threshold,
        )

        # Signal 3: Loss spike
        if train_loss is not None:
            state.update_loss(train_loss)
        loss_ratio, spike_drift = loss_spike_drift(state.loss_history, self.loss_spike_threshold)

        # Combined drift score
        cos_component = (1.0 - cos_sim) * self.weights.get("cosine", 0.4)
        ks_component = ks_stat * self.weights.get("ks", 0.3)
        # Normalize loss ratio: 1.0 → 0, threshold → 1
        loss_norm = max(0.0, min(1.0, (loss_ratio - 1.0) / max(self.loss_spike_threshold - 1.0, 1e-6)))
        spike_component = loss_norm * self.weights.get("loss_spike", 0.3)

        drift_score = cos_component + ks_component + spike_component
        drift_detected = drift_score > self.combined_threshold

        if drift_detected:
            state.drift_count += 1
            logger.warning(
                "[Drift] Client %s DRIFT DETECTED: score=%.4f (cos=%.4f, KS=%.4f, spike=%.4f) "
                "total_events=%d",
                client_id, drift_score, cos_component, ks_component, spike_component,
                state.drift_count,
            )
        else:
            logger.debug(
                "[Drift] Client %s OK: score=%.4f (cos=%.4f, KS=%.4f, spike=%.4f)",
                client_id, drift_score, cos_component, ks_component, spike_component,
            )

        # Update state for next round
        state.update_grad(curr_weights)
        if rssi_values is not None and ks_drift:
            state.rssi_baseline = rssi_values.copy()  # reset baseline on confirmed drift

        audit = {
            "client_id": client_id,
            "drift_detected": drift_detected,
            "drift_score": round(drift_score, 4),
            "cosine_similarity": round(cos_sim, 4),
            "cosine_drift": cos_drift,
            "ks_statistic": round(ks_stat, 4),
            "ks_pvalue": round(ks_pval, 4),
            "ks_drift": ks_drift,
            "loss_ratio": round(loss_ratio, 4),
            "loss_spike": spike_drift,
            "total_drift_events": state.drift_count,
        }
        return audit

    def get_drift_summary(self) -> List[dict]:
        """Return drift state snapshot for all tracked clients."""
        return [
            {
                "client_id": cid,
                "drift_count": state.drift_count,
                "recent_scores": list(state.drift_scores),
            }
            for cid, state in self._states.items()
        ]

    def global_drift_rate(self) -> float:
        """Fraction of clients that have experienced at least one drift event."""
        if not self._states:
            return 0.0
        drifted = sum(1 for s in self._states.values() if s.drift_count > 0)
        return drifted / len(self._states)


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_cosine_drift():
    """Verify cosine drift detects reversed gradients."""
    g1 = np.array([1.0, 0.0, 0.0])
    g2 = np.array([-1.0, 0.0, 0.0])   # opposite direction → cos_sim = -1
    cos_sim, drift = cosine_similarity_drift(g1, g2, threshold=0.3)
    assert drift, "Opposite gradients should trigger cosine drift"
    assert abs(cos_sim - (-1.0)) < 1e-6

    g3 = np.array([1.0, 0.01, 0.0])   # nearly same direction → no drift
    cos_sim3, drift3 = cosine_similarity_drift(g1, g3, threshold=0.3)
    assert not drift3, "Similar gradient direction should not trigger drift"
    print(f"[PASS] Cosine drift: reversed cos={cos_sim:.4f} (drift={drift}), similar cos={cos_sim3:.4f}")


def _test_loss_spike():
    """Verify loss spike detection."""
    history = deque([0.5, 0.4, 0.45, 1.5], maxlen=5)  # last is a spike
    ratio, drift = loss_spike_drift(history, ratio_threshold=2.0)
    # 1.5 / 0.45 ≈ 3.33 > 2.0 → drift
    assert drift, f"Expected drift, ratio={ratio:.4f}"

    history_stable = deque([0.5, 0.48, 0.47], maxlen=5)
    ratio2, drift2 = loss_spike_drift(history_stable, ratio_threshold=2.0)
    assert not drift2, "Stable losses should not trigger drift"
    print(f"[PASS] Loss spike: spike ratio={ratio:.4f} (drift={drift}), stable ratio={ratio2:.4f}")


def _test_registry():
    """Test DriftRegistry with synthetic gradients."""
    registry = DriftRegistry(cosine_threshold=0.3, combined_threshold=0.4)

    weights_round1 = [np.ones((10,)) * 0.5]
    weights_round2 = [np.ones((10,)) * (-0.5)]   # flipped → drift

    registry.update("drone_1", weights_round1, train_loss=0.4)
    audit = registry.update("drone_1", weights_round2, train_loss=0.4)

    assert audit["drift_detected"], "Flipped weights should trigger drift"
    print(f"[PASS] DriftRegistry: drift_score={audit['drift_score']:.4f}, detected={audit['drift_detected']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_cosine_drift()
    _test_loss_spike()
    _test_registry()
    print("All fl/drift.py tests passed.")
