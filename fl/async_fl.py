"""
fl/async_fl.py — Asynchronous Federated Learning (FedBuff)  [FLARE v2]

Emulates asynchronous FL on top of synchronous Flower by maintaining a
server-side circular buffer that collects updates across rounds and
aggregates when the buffer reaches a target size (K_async).

Key Properties:
  - Fast clients do not wait for slow clients (no synchronization barrier)
  - Older updates are penalized by a staleness weight
  - Buffer overflow discards stale updates beyond max_staleness rounds

Staleness Penalty (polynomial decay):
    staleness_weight(s) = 1 / (s + 1)^α
    where s = current_round - submission_round
    and α = staleness_alpha

FedBuff Update Rule:
    θ_{t+1} = θ_t - η · (1/|B|) Σ_{i∈B} staleness_weight(t - t_i) · Δθ_i

Reference:
  Nguyen, J. et al. (2022). Federated Learning with Buffered Asynchronous
  Aggregation. AISTATS 2022. https://arxiv.org/abs/2106.06639

  Xie, C. et al. (2019). Asynchronous Federated Optimization.
  OPT 2019. https://arxiv.org/abs/1903.03934

Emulation Details:
  In Flower, all clients are synchronized per round. To emulate async:
  - The server collects updates from multiple rounds into one buffer
  - When buffer_size updates accumulate (even from different rounds),
    the server performs one asynchronous aggregation step
  - This is a "soft" emulation — a true async system would have no
    global round counter, but the semantics are equivalent for analysis.

Computational overhead: O(buffer_size × d) for staleness weighting.
Memory overhead: O(buffer_size × d) for buffer storage.

Unit-test hooks: run `python -m fl.async_fl` for self-test.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Buffered Update Entry
# ---------------------------------------------------------------------------

@dataclass
class BufferedUpdate:
    """A single client update stored in the async buffer."""
    client_id: str
    weights: List[np.ndarray]
    num_examples: int
    submission_round: int
    train_loss: float = 0.0
    metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Staleness Weight Functions
# ---------------------------------------------------------------------------

def polynomial_staleness_weight(staleness: int, alpha: float = 1.0) -> float:
    """
    Polynomial staleness decay weight.

    FedBuff staleness weight (Nguyen et al. 2022, Eq. 2):
        w(s) = 1 / (s + 1)^α

    Properties:
        w(0) = 1.0  (current round update — no penalty)
        w(1) = 0.5  (one round old, α=1)
        w(s) → 0 as s → ∞

    Args:
        staleness: s = current_round - submission_round
        alpha:     Decay exponent (1.0 = linear decay, 2.0 = quadratic)

    Returns:
        Weight ∈ (0, 1].
    """
    return 1.0 / ((staleness + 1) ** alpha)


def hinge_staleness_weight(staleness: int, max_staleness: int = 10) -> float:
    """
    Hinge-loss staleness weight: linear until max_staleness, then zero.

    w(s) = max(0, 1 - s / max_staleness)

    Alternative to polynomial decay — harder cutoff at max_staleness.
    """
    return max(0.0, 1.0 - staleness / max(max_staleness, 1))


# ---------------------------------------------------------------------------
# Async FL Buffer
# ---------------------------------------------------------------------------

class AsyncFLBuffer:
    """
    Server-side circular buffer for asynchronous federated aggregation.

    Collects client updates (potentially from different rounds) and
    triggers aggregation when buffer_size updates are available.

    FedBuff Algorithm:
        1. Accept update from any client at any time (no barrier)
        2. Add to buffer B with submission timestamp t_i
        3. When |B| ≥ K_async:
           θ ← θ - η · (1/|B|) Σ_{i∈B} w(t - t_i) · Δθ_i
           B ← ∅

    Note: In Flower emulation, "time" is measured in FL rounds.
    A truly async system would use wall-clock time.
    """

    def __init__(
        self,
        buffer_size: int = 4,
        max_staleness: int = 10,
        staleness_alpha: float = 1.0,
        enabled: bool = True,
    ):
        """
        Args:
            buffer_size:     K_async — trigger aggregation when buffer reaches this size.
            max_staleness:   Discard updates older than this many rounds.
            staleness_alpha: Polynomial decay exponent α.
            enabled:         If False, behaves like synchronous FL (buffer_size = ∞).
        """
        self.buffer_size = buffer_size
        self.max_staleness = max_staleness
        self.staleness_alpha = staleness_alpha
        self.enabled = enabled

        self._buffer: List[BufferedUpdate] = []
        self._current_round: int = 0
        self._total_aggregations: int = 0
        self._discarded_updates: int = 0

    def advance_round(self) -> None:
        """Increment the server round counter."""
        self._current_round += 1

    def add_update(self, update: BufferedUpdate) -> None:
        """
        Add a client update to the buffer.
        Silently discards updates that exceed max_staleness.
        """
        staleness = self._current_round - update.submission_round
        if staleness > self.max_staleness:
            logger.warning(
                "[AsyncFL] Discarding stale update from %s (staleness=%d > max=%d)",
                update.client_id, staleness, self.max_staleness,
            )
            self._discarded_updates += 1
            return
        self._buffer.append(update)
        logger.debug(
            "[AsyncFL] Buffered update from %s (staleness=%d, buffer=%d/%d)",
            update.client_id, staleness, len(self._buffer), self.buffer_size,
        )

    def add_updates_from_round(
        self,
        client_ids: List[str],
        weights_list: List[List[np.ndarray]],
        num_examples_list: List[int],
        losses_list: Optional[List[float]] = None,
        metrics_list: Optional[List[dict]] = None,
        submission_round: Optional[int] = None,
    ) -> None:
        """
        Add multiple updates from a Flower round to the buffer.
        Convenience wrapper for integration with Flower's aggregate_fit.
        """
        if submission_round is None:
            submission_round = self._current_round

        for i, (cid, weights, ne) in enumerate(zip(client_ids, weights_list, num_examples_list)):
            update = BufferedUpdate(
                client_id=cid,
                weights=weights,
                num_examples=ne,
                submission_round=submission_round,
                train_loss=losses_list[i] if losses_list else 0.0,
                metrics=metrics_list[i] if metrics_list else {},
            )
            self.add_update(update)

    def is_ready(self) -> bool:
        """Return True if buffer has enough updates to trigger aggregation."""
        if not self.enabled:
            return True  # Sync mode: always aggregate immediately
        return len(self._buffer) >= self.buffer_size

    def aggregate(
        self,
        global_weights: List[np.ndarray],
        server_lr: float = 1.0,
    ) -> Tuple[List[np.ndarray], dict]:
        """
        Perform one FedBuff asynchronous aggregation step.

        FedBuff Update (Nguyen et al. 2022):
            Δ_i = client_weights_i - θ_t       (client update / gradient)
            w_i = staleness_weight(t - t_i)     (staleness penalty)
            θ_{t+1} = θ_t + η · (1/|B|) Σ_i w_i · Δ_i

        Note: This is equivalent to trust-weighted averaging of client models
        when server_lr = 1.0.

        Args:
            global_weights: Current global model θ_t.
            server_lr:      Server learning rate η (typically 1.0).

        Returns:
            (new_global_weights, audit_dict)
        """
        if not self._buffer:
            logger.warning("[AsyncFL] Buffer empty — returning unchanged global weights.")
            return global_weights, {"used": 0, "discarded": self._discarded_updates}

        current_round = self._current_round
        updates_to_use = self._buffer.copy()
        self._buffer.clear()

        # Compute staleness weights
        staleness_weights = []
        for upd in updates_to_use:
            s = current_round - upd.submission_round
            w = polynomial_staleness_weight(s, self.staleness_alpha)
            staleness_weights.append(w)

        # Normalize by total weight
        total_weight = sum(staleness_weights)
        if total_weight < 1e-10:
            norm_weights = [1.0 / len(staleness_weights)] * len(staleness_weights)
        else:
            norm_weights = [w / total_weight for w in staleness_weights]

        # Compute weighted average of client weights
        n_layers = len(global_weights)
        aggregated = []
        for layer_idx in range(n_layers):
            weighted_sum = sum(
                nw * upd.weights[layer_idx]
                for nw, upd in zip(norm_weights, updates_to_use)
            )
            # FedBuff update: θ_new = θ_old + η · (weighted_avg - θ_old) = (1-η)θ + η·avg
            new_layer = (1.0 - server_lr) * global_weights[layer_idx] + server_lr * weighted_sum
            aggregated.append(new_layer)

        self._total_aggregations += 1
        audit = {
            "aggregation_type": "async_fedbuff",
            "used": len(updates_to_use),
            "staleness_weights": [round(w, 4) for w in staleness_weights],
            "norm_weights": [round(w, 4) for w in norm_weights],
            "client_ids": [u.client_id for u in updates_to_use],
            "discarded_total": self._discarded_updates,
            "total_aggregations": self._total_aggregations,
            "current_round": current_round,
        }

        logger.info(
            "[AsyncFL] Aggregated %d buffered updates (round=%d, total_agg=%d, discarded=%d)",
            len(updates_to_use), current_round,
            self._total_aggregations, self._discarded_updates,
        )
        for upd, w, nw in zip(updates_to_use, staleness_weights, norm_weights):
            staleness = current_round - upd.submission_round
            logger.debug(
                "[AsyncFL]   %s: staleness=%d, raw_w=%.4f, norm_w=%.4f",
                upd.client_id, staleness, w, nw,
            )

        return aggregated, audit

    def buffer_status(self) -> dict:
        return {
            "buffer_size_target": self.buffer_size,
            "buffer_current": len(self._buffer),
            "current_round": self._current_round,
            "total_aggregations": self._total_aggregations,
            "discarded_total": self._discarded_updates,
            "ready": self.is_ready(),
        }


# ---------------------------------------------------------------------------
# Convergence Timer (for time-to-convergence benchmarking)
# ---------------------------------------------------------------------------

class ConvergenceTracker:
    """
    Tracks convergence speed for sync vs async FL comparison.

    Records the round at which a target metric threshold is first achieved.
    """

    def __init__(self, target_f1: float = 0.90, metric_key: str = "threat_f1"):
        self.target_f1 = target_f1
        self.metric_key = metric_key
        self._history: List[Tuple[int, float]] = []  # (round, metric_value)
        self._convergence_round: Optional[int] = None

    def record(self, round_num: int, metric_value: float) -> None:
        self._history.append((round_num, metric_value))
        if self._convergence_round is None and metric_value >= self.target_f1:
            self._convergence_round = round_num
            logger.info(
                "[AsyncFL] Convergence achieved at round %d (target=%.4f, achieved=%.4f)",
                round_num, self.target_f1, metric_value,
            )

    @property
    def convergence_round(self) -> Optional[int]:
        return self._convergence_round

    def summary(self) -> dict:
        return {
            "target_metric": self.metric_key,
            "target_value": self.target_f1,
            "convergence_round": self._convergence_round,
            "converged": self._convergence_round is not None,
            "best_value": max((m for _, m in self._history), default=0.0),
        }


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_staleness_weights():
    """Verify polynomial staleness decay is monotonically decreasing."""
    weights = [polynomial_staleness_weight(s, alpha=1.0) for s in range(10)]
    for i in range(len(weights) - 1):
        assert weights[i] > weights[i + 1], f"Staleness weight should decrease: {weights}"
    assert abs(weights[0] - 1.0) < 1e-6, "Staleness 0 should have weight 1.0"
    print(f"[PASS] Staleness weights: {[round(w, 4) for w in weights[:5]]}")


def _test_buffer_aggregation():
    """Test buffer collects and aggregates correctly."""
    global_weights = [np.zeros(10, dtype=np.float32)]
    buffer = AsyncFLBuffer(buffer_size=2, staleness_alpha=1.0)

    # Add 2 updates with same weights (ones)
    buffer.add_update(BufferedUpdate(
        client_id="c1", weights=[np.ones(10, dtype=np.float32)],
        num_examples=100, submission_round=0
    ))
    buffer.add_update(BufferedUpdate(
        client_id="c2", weights=[np.ones(10, dtype=np.float32)],
        num_examples=100, submission_round=0
    ))

    assert buffer.is_ready(), "Buffer should be ready after 2 updates"
    new_weights, audit = buffer.aggregate(global_weights, server_lr=1.0)
    assert np.allclose(new_weights[0], 1.0), f"Expected ones, got {new_weights[0][:5]}"
    assert audit["used"] == 2
    print(f"[PASS] Buffer aggregation: {audit}")


def _test_stale_discard():
    """Updates older than max_staleness should be discarded."""
    buffer = AsyncFLBuffer(buffer_size=1, max_staleness=3)
    buffer._current_round = 10  # simulate 10 rounds passed

    buffer.add_update(BufferedUpdate(
        client_id="old_client",
        weights=[np.ones(5)],
        num_examples=50,
        submission_round=2,  # staleness = 10 - 2 = 8 > max_staleness=3
    ))
    assert len(buffer._buffer) == 0, "Stale update should be discarded"
    assert buffer._discarded_updates == 1
    print(f"[PASS] Stale discard: discarded_updates={buffer._discarded_updates}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_staleness_weights()
    _test_buffer_aggregation()
    _test_stale_discard()
    print("All fl/async_fl.py tests passed.")
