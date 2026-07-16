"""
fl/selection.py — Adaptive Client Selection  [FLARE v2]

Implements two complementary selection strategies:

  1. Power-of-Choice (PoCo / Oort-style)
     Biases selection toward clients with high local loss (exploits
     informative clients — those who would contribute most to convergence).

  2. UCB (Upper Confidence Bound)
     Balances exploration (try under-used clients) with exploitation
     (prefer high-trust, high-quality clients). Based on multi-armed
     bandit UCB1 algorithm.

  3. Combined
     final_score_i = λ · poco_score_i + (1-λ) · ucb_score_i

All strategies are independently selectable via fl_config.yaml.

References:
  Lai, F. et al. (2021). Oort: Efficient Federated Learning via Guided
  Participant Selection. OSDI 2021. https://arxiv.org/abs/2010.06081

  Lai, F. et al. (2021). FedScale: Benchmarking Model and System Performance
  of Federated Learning at Scale. https://arxiv.org/abs/2105.11367

  Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time Analysis of
  the Multiarmed Bandit Problem. Machine Learning, 47(2-3), 235–256.

Power-of-Choice Algorithm:
    1. Sample d = K × candidate_multiplier candidates uniformly
    2. Estimate local loss L̂_i for each candidate (from last report)
    3. Select K clients with highest estimated loss

UCB1 Score:
    score_i(t) = τ_i(t) + β · √(ln(t) / n_i)
    where n_i = number of rounds client i has participated

Combined Score:
    score_i = λ · poco_i + (1-λ) · ucb_i

Computational overhead: O(N log N) for sorting candidates — negligible.

Unit-test hooks: run `python -m fl.selection` for self-test.
"""

from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client Performance State (for selection decisions)
# ---------------------------------------------------------------------------

class ClientSelectionState:
    """
    Tracks selection-relevant statistics for each candidate client.

    Attributes:
        client_id:         Client identifier.
        last_loss:         Most recent training loss reported.
        rounds_selected:   Total rounds this client was selected.
        rounds_available:  Total rounds this client was available.
        last_latency:      Round completion time (for Oort system utility).
    """

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.last_loss: float = float("inf")  # High → prefer in PoCo
        self.rounds_selected: int = 0
        self.rounds_available: int = 0
        self.last_latency: float = 0.0

    def update(
        self,
        loss: Optional[float] = None,
        selected: bool = True,
        latency: float = 0.0,
    ) -> None:
        self.rounds_available += 1
        if selected:
            self.rounds_selected += 1
        if loss is not None:
            self.last_loss = loss
        self.last_latency = latency

    def selection_rate(self) -> float:
        if self.rounds_available == 0:
            return 0.0
        return self.rounds_selected / self.rounds_available


# ---------------------------------------------------------------------------
# Adaptive Client Selector
# ---------------------------------------------------------------------------

class AdaptiveClientSelector:
    """
    Adaptive client selection engine for the FL server.

    Supports three strategies: "poco", "ucb", "combined", "random".

    Usage:
        selector = AdaptiveClientSelector(strategy="combined", ...)
        selected = selector.select(available_clients, k=5, round_num=3)
        selector.update_stats(client_id, loss=0.34, selected=True)
    """

    def __init__(
        self,
        strategy: str = "combined",
        candidate_multiplier: int = 2,
        ucb_beta: float = 0.5,
        poco_weight: float = 0.6,
        ucb_weight: float = 0.4,
        trust_registry: Optional[object] = None,  # fl.trust.TrustRegistry
        enabled: bool = True,
    ):
        """
        Args:
            strategy:             Selection strategy: "poco"|"ucb"|"combined"|"random".
            candidate_multiplier: PoCo oversampling factor d = K × multiplier.
            ucb_beta:             UCB exploration coefficient β.
            poco_weight:          λ: weight of PoCo score in combined strategy.
            ucb_weight:           (1-λ): weight of UCB score in combined.
            trust_registry:       TrustRegistry for trust-aware UCB.
            enabled:              If False, falls back to random selection.
        """
        self.strategy = strategy if enabled else "random"
        self.candidate_multiplier = candidate_multiplier
        self.ucb_beta = ucb_beta
        self.poco_weight = poco_weight
        self.ucb_weight = ucb_weight
        self.trust_registry = trust_registry

        self._states: Dict[str, ClientSelectionState] = {}
        self._selection_history: List[List[str]] = []
        self._total_rounds: int = 0

    def _get_state(self, client_id: str) -> ClientSelectionState:
        if client_id not in self._states:
            self._states[client_id] = ClientSelectionState(client_id)
        return self._states[client_id]

    def _poco_score(self, client_id: str) -> float:
        """
        Power-of-Choice score: clients with higher last known loss are preferred.

        Oort utility (simplified):
            poco_score_i = L̂_i (last reported training loss)

        Clients with high loss are further from convergence and will provide
        more informative gradients. (Lai et al. 2021, Section 3.2)
        """
        state = self._get_state(client_id)
        # Clip to reasonable range; INF → large value for first round
        loss = min(state.last_loss, 10.0)
        return loss

    def _ucb_score(self, client_id: str, round_num: int) -> float:
        """
        UCB1 score for exploration-exploitation balance.

        UCB1 Formula (Auer et al. 2002):
            score_i(t) = X̄_i + β · √(ln(t) / n_i)

        where:
            X̄_i = mean reward (proxy: trust score τ_i)
            n_i  = number of times client i was selected
            t    = current round number
            β    = exploration coefficient

        Clients selected fewer times get an exploration bonus.
        """
        state = self._get_state(client_id)

        # Mean reward: use trust score if registry available, else selection rate
        if self.trust_registry is not None:
            trust_state = self.trust_registry.get(client_id)
            mean_reward = trust_state.trust_score
        else:
            mean_reward = max(0.5, state.selection_rate())

        # Exploration bonus: UCB1
        n_selected = max(state.rounds_selected, 1)
        t = max(round_num, 1)
        exploration_bonus = self.ucb_beta * math.sqrt(math.log(t) / n_selected)

        return mean_reward + exploration_bonus

    def _normalize_scores(self, scores: Dict[str, float]) -> Dict[str, float]:
        """Min-max normalize scores to [0, 1]."""
        if not scores:
            return scores
        min_s = min(scores.values())
        max_s = max(scores.values())
        rng = max_s - min_s
        if rng < 1e-10:
            return {k: 1.0 for k in scores}
        return {k: (v - min_s) / rng for k, v in scores.items()}

    def select(
        self,
        available_clients: List[str],
        k: int,
        round_num: int,
    ) -> List[str]:
        """
        Select K clients from the available pool.

        Args:
            available_clients: All available client IDs this round.
            k:                 Number of clients to select.
            round_num:         Current FL round number (1-indexed).

        Returns:
            List of K selected client IDs.
        """
        n_available = len(available_clients)
        k = min(k, n_available)

        if k == n_available or self.strategy == "random":
            selected = available_clients[:] if k == n_available else random.sample(available_clients, k)
            self._record_selection(available_clients, selected, round_num)
            return selected

        if self.strategy == "poco":
            selected = self._select_poco(available_clients, k, round_num)
        elif self.strategy == "ucb":
            selected = self._select_ucb(available_clients, k, round_num)
        elif self.strategy == "combined":
            selected = self._select_combined(available_clients, k, round_num)
        else:
            selected = random.sample(available_clients, k)

        self._record_selection(available_clients, selected, round_num)
        logger.info(
            "[Selection] Round %d | strategy=%s | available=%d | selected=%d → %s",
            round_num, self.strategy, n_available, k, selected,
        )
        return selected

    def _select_poco(
        self,
        available: List[str],
        k: int,
        round_num: int,
    ) -> List[str]:
        """
        Power-of-Choice selection:
          1. Sample d = min(k × multiplier, N) candidates uniformly
          2. Score each candidate by last known loss
          3. Return top-k by score
        """
        d = min(k * self.candidate_multiplier, len(available))
        candidates = random.sample(available, d)
        scores = {c: self._poco_score(c) for c in candidates}
        sorted_candidates = sorted(scores, key=scores.get, reverse=True)
        return sorted_candidates[:k]

    def _select_ucb(
        self,
        available: List[str],
        k: int,
        round_num: int,
    ) -> List[str]:
        """UCB-based selection: score all available clients, return top-k."""
        scores = {c: self._ucb_score(c, round_num) for c in available}
        return sorted(scores, key=scores.get, reverse=True)[:k]

    def _select_combined(
        self,
        available: List[str],
        k: int,
        round_num: int,
    ) -> List[str]:
        """
        Combined PoCo + UCB selection:
            final_score_i = λ · poco_norm_i + (1-λ) · ucb_norm_i
        """
        # PoCo: sample d candidates
        d = min(k * self.candidate_multiplier, len(available))
        candidates = random.sample(available, d)

        poco_scores = {c: self._poco_score(c) for c in candidates}
        ucb_scores = {c: self._ucb_score(c, round_num) for c in candidates}

        poco_norm = self._normalize_scores(poco_scores)
        ucb_norm = self._normalize_scores(ucb_scores)

        combined = {
            c: self.poco_weight * poco_norm[c] + self.ucb_weight * ucb_norm.get(c, 0.5)
            for c in candidates
        }
        sorted_combined = sorted(combined, key=combined.get, reverse=True)
        selected = sorted_combined[:k]

        logger.debug(
            "[Selection] Combined scores: %s",
            {c: round(combined[c], 4) for c in selected},
        )
        return selected

    def _record_selection(
        self,
        available: List[str],
        selected: List[str],
        round_num: int,
    ) -> None:
        """Update selection state for all available clients."""
        self._total_rounds = round_num
        selected_set = set(selected)
        for cid in available:
            state = self._get_state(cid)
            state.update(selected=(cid in selected_set))
        self._selection_history.append(list(selected))

    def update_client_stats(
        self,
        client_id: str,
        loss: Optional[float] = None,
        latency: float = 0.0,
    ) -> None:
        """
        Update client statistics after a round completes.
        Called by the server after receiving client results.

        Args:
            client_id: Client identifier.
            loss:      Training loss from fit() metrics.
            latency:   Round completion time in seconds.
        """
        state = self._get_state(client_id)
        if loss is not None:
            state.last_loss = loss
        state.last_latency = latency

    def get_selection_counts(self) -> Dict[str, int]:
        """Return number of times each client was selected (for fairness metrics)."""
        return {cid: s.rounds_selected for cid, s in self._states.items()}

    def jains_fairness(self) -> float:
        """Jain's Fairness Index across all known clients."""
        counts = list(self.get_selection_counts().values())
        if not counts:
            return 1.0
        from fl.trust import jains_fairness_index
        return jains_fairness_index(counts)

    def summary(self) -> dict:
        counts = self.get_selection_counts()
        return {
            "strategy": self.strategy,
            "total_rounds": self._total_rounds,
            "n_clients": len(self._states),
            "selection_counts": counts,
            "jains_fairness": round(self.jains_fairness(), 4),
            "mean_selection_rate": (
                sum(s.selection_rate() for s in self._states.values()) / len(self._states)
                if self._states else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_poco_selection():
    """PoCo should prefer high-loss clients."""
    selector = AdaptiveClientSelector(
        strategy="poco", candidate_multiplier=3, enabled=True
    )
    clients = [f"drone_{i}" for i in range(10)]

    # Assign losses: drone_9 has highest loss
    for i, c in enumerate(clients):
        selector.update_client_stats(c, loss=float(i) * 0.1)

    # Run selection multiple times → drone_9 should appear most
    selection_counts: Dict[str, int] = defaultdict(int)
    for r in range(20):
        selected = selector.select(clients, k=3, round_num=r + 1)
        for s in selected:
            selection_counts[s] += 1

    # High-loss clients (drone_7, 8, 9) should be selected more
    high_loss_total = selection_counts["drone_9"] + selection_counts["drone_8"]
    low_loss_total = selection_counts["drone_0"] + selection_counts["drone_1"]
    assert high_loss_total >= low_loss_total, "PoCo should favor high-loss clients"
    print(f"[PASS] PoCo selection: high-loss={high_loss_total}, low-loss={low_loss_total}")


def _test_ucb_exploration():
    """UCB should give bonus to under-explored clients."""
    selector = AdaptiveClientSelector(
        strategy="ucb", ucb_beta=1.0, enabled=True
    )
    clients = [f"drone_{i}" for i in range(5)]

    # Force drone_0 to be under-explored
    for r in range(10):
        selected = selector.select(clients, k=2, round_num=r + 1)

    counts = selector.get_selection_counts()
    # All clients should eventually be selected (exploration)
    assert all(v > 0 for v in counts.values()), "UCB should explore all clients"
    print(f"[PASS] UCB exploration: {counts}")


def _test_fairness():
    """Test Jain's fairness computation."""
    selector = AdaptiveClientSelector(strategy="random", enabled=True)
    clients = ["a", "b", "c"]
    for r in range(30):
        selector.select(clients, k=2, round_num=r + 1)
    j = selector.jains_fairness()
    assert j > 0.8, f"Random selection should be fairly fair, got J={j:.4f}"
    print(f"[PASS] Jain's fairness for random selection: J={j:.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_poco_selection()
    _test_ucb_exploration()
    _test_fairness()
    print("All fl/selection.py tests passed.")
