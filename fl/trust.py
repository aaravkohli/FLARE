"""
fl/trust.py — Trust Scores + Reputation Model  [FLARE v2]

Implements a per-client trust/reputation system for the FL server:
  - EMA-based trust score updated each round
  - Multi-signal quality assessment (validation accuracy, anomaly score, participation)
  - Quarantine mechanism for persistently malicious/unreliable clients
  - Trust-weighted aggregation weights for FedAvg

Reference:
  Cao, X. et al. (2020). FLTrust: Byzantine-robust Federated Learning via Trust
  Bootstrapping. NDSS 2021. https://arxiv.org/abs/2012.13995

  Blanchard, P. et al. (2017). Machine Learning with Adversaries: Byzantine
  Tolerant Gradient Descent. NeurIPS 2017. https://arxiv.org/abs/1703.02757

Trust Score Update Rule:
    τ_i(t) = α · τ_i(t-1) + (1 - α) · quality_i(t)

Quality Function:
    quality_i(t) = val_acc_i(t) · (1 - norm_anomaly_i(t)) · (1 + participation_bonus)
                   clipped to [0, 1]

Quarantine:
    if τ_i(t) < τ_min for quarantine_trigger_rounds → exclude for quarantine_rounds

Computational overhead: O(n_clients) per round — negligible.
Memory overhead: O(n_clients) for score history — negligible.

Unit-test hooks: run `python -m fl.trust` for self-test.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client Trust State
# ---------------------------------------------------------------------------

class ClientTrustState:
    """
    Tracks all trust-related state for a single client across FL rounds.

    Attributes:
        trust_score:   Current EMA trust score τ_i ∈ [0, 1].
        rounds_participated: Total rounds this client has participated in.
        rounds_consecutive:  Consecutive rounds without missing.
        quarantine_remaining: Rounds left in quarantine (0 = not quarantined).
        anomaly_history:     Recent anomaly scores (bounded deque).
        loss_history:        Recent validation losses.
    """

    def __init__(self, client_id: str, initial_trust: float = 1.0):
        self.client_id = client_id
        self.trust_score: float = initial_trust
        self.rounds_participated: int = 0
        self.rounds_consecutive: int = 0
        self.quarantine_remaining: int = 0
        self.anomaly_history: deque = deque(maxlen=10)
        self.loss_history: deque = deque(maxlen=10)
        self.accuracy_history: deque = deque(maxlen=10)

    def is_quarantined(self) -> bool:
        return self.quarantine_remaining > 0

    def decrement_quarantine(self) -> None:
        if self.quarantine_remaining > 0:
            self.quarantine_remaining -= 1
            if self.quarantine_remaining == 0:
                logger.info("[Trust] Client %s released from quarantine.", self.client_id)

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "trust_score": round(self.trust_score, 4),
            "rounds_participated": self.rounds_participated,
            "quarantine_remaining": self.quarantine_remaining,
            "is_quarantined": self.is_quarantined(),
            "mean_recent_anomaly": (
                sum(self.anomaly_history) / len(self.anomaly_history)
                if self.anomaly_history else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# Trust Registry (server-side, one instance per FL run)
# ---------------------------------------------------------------------------

class TrustRegistry:
    """
    Central registry for all client trust scores and reputation tracking.

    Used by the FL server to:
      1. Update trust scores after each round (given quality signals).
      2. Compute trust-weighted aggregation weights.
      3. Decide quarantine actions.
      4. Report trust metrics.

    Trust Score Update (EMA):
        τ_i(t) = α · τ_i(t-1) + (1-α) · quality_i(t)

    where quality_i combines:
        q_i = val_acc_i · (1 - norm_anomaly_i) · participation_factor
        q_i = clip(q_i, 0, 1)
    """

    def __init__(
        self,
        alpha: float = 0.9,
        tau_min: float = 0.2,
        quarantine_rounds: int = 3,
        initial_trust: float = 1.0,
        participation_bonus: float = 0.05,
    ):
        """
        Args:
            alpha:               EMA decay factor (higher = more weight on history).
            tau_min:             Minimum trust before quarantine trigger.
            quarantine_rounds:   Rounds to quarantine a low-trust client.
            initial_trust:       Starting trust score for new clients.
            participation_bonus: Additive bonus for consistent participation.
        """
        self.alpha = alpha
        self.tau_min = tau_min
        self.quarantine_rounds = quarantine_rounds
        self.initial_trust = initial_trust
        self.participation_bonus = participation_bonus
        self._clients: Dict[str, ClientTrustState] = {}
        self._low_trust_streak: Dict[str, int] = defaultdict(int)

    def register(self, client_id: str) -> ClientTrustState:
        """Register a new client (idempotent)."""
        if client_id not in self._clients:
            self._clients[client_id] = ClientTrustState(
                client_id=client_id,
                initial_trust=self.initial_trust,
            )
            logger.info("[Trust] Registered new client: %s (τ=%.2f)", client_id, self.initial_trust)
        return self._clients[client_id]

    def get(self, client_id: str) -> ClientTrustState:
        """Get (or register) client state."""
        return self.register(client_id)

    def update(
        self,
        client_id: str,
        anomaly_score: float,
        val_accuracy: Optional[float] = None,
        val_loss: Optional[float] = None,
        participated: bool = True,
    ) -> float:
        """
        Update trust score for a client after a round.

        Quality Signal Computation:
            accuracy_component = val_acc ∈ [0, 1] (or 1.0 if unknown)
            anomaly_component  = 1 - min(anomaly_score / z_threshold, 1)
            participation_component = 1 + participation_bonus (if participated)

            quality = accuracy_component × anomaly_component × participation_component
            quality = clip(quality, 0, 1)

        EMA Update:
            τ_i(t) = α · τ_i(t-1) + (1-α) · quality

        Args:
            client_id:     Client identifier.
            anomaly_score: Z-score anomaly score (higher = more anomalous).
            val_accuracy:  Client's local validation accuracy (optional).
            val_loss:      Client's local validation loss (optional).
            participated:  Whether this client participated this round.

        Returns:
            New trust score τ_i(t).
        """
        state = self.register(client_id)

        # Update history
        state.anomaly_history.append(anomaly_score)
        if val_accuracy is not None:
            state.accuracy_history.append(val_accuracy)
        if val_loss is not None:
            state.loss_history.append(val_loss)

        if not participated:
            state.rounds_consecutive = 0
            # Mild trust decay for non-participation
            state.trust_score = max(0.0, self.alpha * state.trust_score)
            logger.debug("[Trust] %s did not participate. τ=%.4f", client_id, state.trust_score)
            return state.trust_score

        state.rounds_participated += 1
        state.rounds_consecutive += 1

        # Compute quality components
        # 1. Accuracy component (default 0.5 if unknown — neutral)
        if state.accuracy_history:
            acc_comp = float(min(state.accuracy_history[-1], 1.0))
        else:
            acc_comp = 0.5  # neutral prior

        # 2. Anomaly component: 1 - normalized anomaly score
        #    anomaly_score ~2.5 at threshold → anomaly_comp → 0
        #    anomaly_score ~0.0 (clean) → anomaly_comp → 1.0
        norm_anomaly = min(anomaly_score / 5.0, 1.0)  # normalize by 5σ
        anomaly_comp = 1.0 - norm_anomaly

        # 3. Participation bonus (long-running, consistent clients earn trust)
        participation_comp = 1.0 + min(
            self.participation_bonus * state.rounds_consecutive,
            0.20,  # cap bonus at +20%
        )

        # Combined quality
        quality = acc_comp * anomaly_comp * participation_comp
        quality = float(max(0.0, min(1.0, quality)))

        # EMA trust update
        old_tau = state.trust_score
        state.trust_score = self.alpha * old_tau + (1.0 - self.alpha) * quality

        logger.debug(
            "[Trust] %s: quality=%.4f (acc=%.3f, anomaly_comp=%.3f, part=%.2f) "
            "τ: %.4f → %.4f",
            client_id, quality, acc_comp, anomaly_comp, participation_comp,
            old_tau, state.trust_score,
        )

        # Quarantine decision
        if state.trust_score < self.tau_min:
            self._low_trust_streak[client_id] += 1
            if self._low_trust_streak[client_id] >= 2:  # 2 consecutive low-trust rounds
                state.quarantine_remaining = self.quarantine_rounds
                logger.warning(
                    "[Trust] Client %s QUARANTINED for %d rounds (τ=%.4f < %.4f)",
                    client_id, self.quarantine_rounds, state.trust_score, self.tau_min,
                )
                self._low_trust_streak[client_id] = 0
        else:
            self._low_trust_streak[client_id] = 0

        return state.trust_score

    def decrement_all_quarantines(self) -> None:
        """Call at the end of each round to decrement quarantine counters."""
        for state in self._clients.values():
            state.decrement_quarantine()

    def get_aggregation_weights(
        self,
        client_ids: List[str],
        num_examples: Optional[List[int]] = None,
    ) -> List[float]:
        """
        Compute trust-weighted aggregation weights for a list of clients.

        Weight formula:
            raw_i = τ_i × (n_i / n_total)   [trust × data size]
            w_i   = raw_i / Σ_j raw_j       [normalized]

        If num_examples is None, uses uniform data weighting.

        Args:
            client_ids:   Ordered list of participating client IDs.
            num_examples: Number of training samples per client.

        Returns:
            Normalized aggregation weights summing to 1.0.
        """
        n = len(client_ids)
        if num_examples is None:
            num_examples = [1] * n

        total_examples = max(sum(num_examples), 1)

        raw_weights = []
        for cid, ne in zip(client_ids, num_examples):
            state = self.register(cid)
            tau = state.trust_score
            data_fraction = ne / total_examples
            raw_weights.append(tau * data_fraction)

        total_raw = sum(raw_weights)
        if total_raw == 0.0:
            # Fallback: uniform weights
            return [1.0 / n] * n

        normalized = [w / total_raw for w in raw_weights]
        return normalized

    def get_eligible_clients(self, client_ids: List[str]) -> List[str]:
        """Return only non-quarantined clients from the list."""
        eligible = [cid for cid in client_ids if not self.get(cid).is_quarantined()]
        quarantined = set(client_ids) - set(eligible)
        if quarantined:
            logger.warning("[Trust] Quarantined clients excluded: %s", sorted(quarantined))
        return eligible

    def snapshot(self) -> List[dict]:
        """Return a snapshot of all client trust states for metrics logging."""
        return [state.to_dict() for state in self._clients.values()]

    def mean_trust(self) -> float:
        """Compute mean trust score across all registered clients."""
        if not self._clients:
            return 1.0
        return sum(s.trust_score for s in self._clients.values()) / len(self._clients)

    def quarantine_rate(self) -> float:
        """Fraction of clients currently quarantined."""
        if not self._clients:
            return 0.0
        n_quarantined = sum(1 for s in self._clients.values() if s.is_quarantined())
        return n_quarantined / len(self._clients)

    def summary(self) -> dict:
        return {
            "n_clients": len(self._clients),
            "mean_trust": round(self.mean_trust(), 4),
            "quarantine_rate": round(self.quarantine_rate(), 4),
            "n_quarantined": sum(1 for s in self._clients.values() if s.is_quarantined()),
            "alpha": self.alpha,
            "tau_min": self.tau_min,
        }


# ---------------------------------------------------------------------------
# Trust-Weighted FedAvg
# ---------------------------------------------------------------------------

def trust_weighted_fedavg(
    client_weights: List[List["np.ndarray"]],
    agg_weights: List[float],
) -> List["np.ndarray"]:
    """
    Weighted average of client model updates using trust-based weights.

    Trust-Weighted FedAvg:
        θ_global = Σ_i w_i · θ_i    where Σ_i w_i = 1
        w_i = (τ_i · n_i) / Σ_j (τ_j · n_j)

    This replaces standard FedAvg where w_i = n_i / n_total.

    Args:
        client_weights: List of client model weight lists.
        agg_weights:    Normalized aggregation weights (from TrustRegistry).

    Returns:
        Aggregated global model weights.
    """
    import numpy as np

    if not client_weights:
        raise ValueError("No client weights to aggregate.")
    if abs(sum(agg_weights) - 1.0) > 1e-6:
        # Re-normalize if needed
        total = sum(agg_weights)
        agg_weights = [w / total for w in agg_weights]

    n_layers = len(client_weights[0])
    aggregated = []
    for layer_idx in range(n_layers):
        weighted = sum(
            w * ws[layer_idx]
            for w, ws in zip(agg_weights, client_weights)
        )
        aggregated.append(weighted)

    logger.debug(
        "Trust-weighted FedAvg: %d clients, weights=[%s]",
        len(client_weights),
        ", ".join(f"{w:.4f}" for w in agg_weights),
    )
    return aggregated


# ---------------------------------------------------------------------------
# Jain's Fairness Index (for client selection evaluation)
# ---------------------------------------------------------------------------

def jains_fairness_index(selection_counts: List[int]) -> float:
    """
    Compute Jain's Fairness Index for client selection frequency.

    J(x) = (Σ x_i)² / (n · Σ x_i²)

    J = 1.0 → perfectly fair (all clients selected equally)
    J → 1/n → maximally unfair (only one client selected)

    Reference:
        Jain, R. et al. (1984). A Quantitative Measure of Fairness and
        Discrimination for Resource Allocation in Shared Systems.
        https://arxiv.org/abs/cs/9809099

    Args:
        selection_counts: Number of times each client was selected.

    Returns:
        Jain's fairness index ∈ [1/n, 1].
    """
    import numpy as np
    x = np.array(selection_counts, dtype=np.float64)
    n = len(x)
    if n == 0 or x.sum() == 0:
        return 1.0
    return float((x.sum() ** 2) / (n * (x ** 2).sum()))


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_trust_update():
    """Test EMA trust update with clean and anomalous clients."""
    registry = TrustRegistry(alpha=0.9, tau_min=0.2, quarantine_rounds=2)

    # Clean client: low anomaly, high accuracy
    for _ in range(5):
        registry.update("drone_1", anomaly_score=0.1, val_accuracy=0.95)
    tau_clean = registry.get("drone_1").trust_score
    assert tau_clean > 0.8, f"Clean client should have high trust, got {tau_clean:.4f}"

    # Adversarial client: high anomaly, low accuracy
    for _ in range(5):
        registry.update("drone_bad", anomaly_score=4.5, val_accuracy=0.10)
    tau_bad = registry.get("drone_bad").trust_score
    assert tau_bad < tau_clean, "Adversarial client should have lower trust"

    print(f"[PASS] Trust update: clean={tau_clean:.4f}, adversarial={tau_bad:.4f}")


def _test_quarantine():
    """Test quarantine fires after consecutive low-trust rounds."""
    registry = TrustRegistry(alpha=0.5, tau_min=0.5, quarantine_rounds=3)
    # Force very low quality scores → trust drops below tau_min
    for _ in range(5):
        registry.update("drone_evil", anomaly_score=10.0, val_accuracy=0.01)

    state = registry.get("drone_evil")
    assert state.is_quarantined(), "Client with persistent low trust should be quarantined"
    print(f"[PASS] Quarantine: drone_evil quarantined for {state.quarantine_remaining} rounds")


def _test_agg_weights():
    """Test aggregation weights are normalized and trust-influenced."""
    registry = TrustRegistry()
    registry.update("c1", anomaly_score=0.1, val_accuracy=0.95)
    registry.update("c2", anomaly_score=3.5, val_accuracy=0.30)

    weights = registry.get_aggregation_weights(["c1", "c2"], num_examples=[100, 100])
    assert abs(sum(weights) - 1.0) < 1e-6, "Weights must sum to 1"
    assert weights[0] > weights[1], "Higher-trust client should have more weight"
    print(f"[PASS] Aggregation weights: c1={weights[0]:.4f}, c2={weights[1]:.4f}")


def _test_jains_fairness():
    """Test Jain's fairness index edge cases."""
    assert abs(jains_fairness_index([5, 5, 5]) - 1.0) < 1e-6, "Uniform → J=1"
    assert jains_fairness_index([10, 0, 0]) < 0.5, "Unfair → J < 0.5"
    print(f"[PASS] Jain's fairness: uniform={jains_fairness_index([5,5,5]):.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_trust_update()
    _test_quarantine()
    _test_agg_weights()
    _test_jains_fairness()
    print("All fl/trust.py tests passed.")
