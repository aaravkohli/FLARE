"""Byzantine update evidence and persistent client reputation for FLARE.

The live ``SecureFedAvgV2`` path uses ``ClientIdentityRegistry``,
``UpdateAnalyzer``, and ``TrustManager``. All security-relevant quality signals
in that path are calculated by the server from submitted parameter deltas; a
client's self-reported accuracy cannot increase its aggregation weight.

The older ``TrustRegistry`` near the end of this file is retained only for the
disabled experimental client-selector compatibility API. It accepts reported
quality values and must not be described as the Byzantine security decision.

Reference:
  Cao, X. et al. (2020). FLTrust: Byzantine-robust Federated Learning via Trust
  Bootstrapping. NDSS 2021. https://arxiv.org/abs/2012.13995

  Blanchard, P. et al. (2017). Machine Learning with Adversaries: Byzantine
  Tolerant Gradient Descent. NeurIPS 2017. https://arxiv.org/abs/1703.02757

Production trust update rule:
    τ_i(t) = α · τ_i(t-1) + (1 - α) · quality_i(t)

Quality Function:
    quality_i(t) = 1 - robust_update_deviation_i(t), clipped to [0, 1]

Quarantine:
    if τ_i(t) < τ_min for quarantine_trigger_rounds → exclude for quarantine_rounds

The analyzer flattens model deltas, so its dominant work is linear in the total
number of submitted parameters plus coordinate-wise robust statistics.

Unit-test hooks: run `python -m fl.trust` for self-test.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ClientIdentityRegistry:
    """Bind a Flower connection ID to one claimed drone ID for the server run.

    This prevents a connected insider from rotating names to reset reputation or
    claiming the same drone identity from two concurrent connections. It is not
    a substitute for production mTLS/device certificates.
    """

    def __init__(
        self,
        allowed_client_ids: Optional[Sequence[str]] = None,
        allowed_client_resolver: Optional[Callable[[], Sequence[str]]] = None,
    ):
        self.allowed_client_ids: Optional[Set[str]] = (
            {str(value) for value in allowed_client_ids}
            if allowed_client_ids is not None
            else None
        )
        self.allowed_client_resolver = allowed_client_resolver
        self._proxy_to_client: Dict[str, str] = {}
        self._round_client_to_proxy: Dict[str, str] = {}

    def begin_round(self) -> None:
        """Reset duplicate claims while retaining each connection's identity.

        Flower can assign a new proxy ID after a legitimate reconnect. Duplicate
        claims are therefore rejected within a round, while a later reconnect
        can resume the same durable drone reputation under its claimed ID.
        """
        self._round_client_to_proxy.clear()

    def resolve(self, proxy_id: str, claimed_client_id: str) -> Tuple[str, Optional[str]]:
        proxy_id = str(proxy_id)
        claimed_client_id = str(claimed_client_id).strip()
        if not claimed_client_id:
            return f"unverified:{proxy_id}", "missing client identity claim"
        allowed = (
            {str(value) for value in self.allowed_client_resolver()}
            if self.allowed_client_resolver is not None
            else self.allowed_client_ids
        )
        if allowed is not None and claimed_client_id not in allowed:
            return f"unverified:{proxy_id}", f"client identity {claimed_client_id!r} is not allowed"

        existing_claim = self._proxy_to_client.get(proxy_id)
        if existing_claim is not None and existing_claim != claimed_client_id:
            return existing_claim, (
                f"connection {proxy_id!r} changed identity from {existing_claim!r} "
                f"to {claimed_client_id!r}"
            )
        existing_proxy = self._round_client_to_proxy.get(claimed_client_id)
        if existing_proxy is not None and existing_proxy != proxy_id:
            return f"unverified:{proxy_id}", (
                f"duplicate identity {claimed_client_id!r} already bound to another connection"
            )

        self._proxy_to_client[proxy_id] = claimed_client_id
        self._round_client_to_proxy[claimed_client_id] = proxy_id
        return claimed_client_id, None


def _robust_scale(values: np.ndarray, epsilon: float = 1e-12) -> Tuple[float, float]:
    """Return ``(median, scale)`` using MAD with an IQR fallback.

    The final relative-scale fallback is important for small FL populations: if
    two drones agree exactly and a third is poisoned, MAD is zero because the
    majority is identical. Treating that as "no anomaly" would be unsafe.
    """
    values = np.asarray(values, dtype=np.float64)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad > epsilon:
        return median, mad / 0.6744897501960817
    q1, q3 = np.percentile(values, [25.0, 75.0])
    iqr_scale = float(q3 - q1) / 1.3489795003921634
    if iqr_scale > epsilon and np.count_nonzero(np.isclose(values, median)) < len(values) / 2:
        return median, iqr_scale
    return median, max(abs(median) * 0.05, epsilon)


def _robust_upper_z(values: Sequence[float], cap: float = 20.0) -> np.ndarray:
    values_arr = np.asarray(values, dtype=np.float64)
    median, scale = _robust_scale(values_arr)
    return np.clip(np.maximum(0.0, values_arr - median) / scale, 0.0, cap)


def _robust_lower_z(values: Sequence[float], cap: float = 20.0) -> np.ndarray:
    values_arr = np.asarray(values, dtype=np.float64)
    median, scale = _robust_scale(values_arr)
    return np.clip(np.maximum(0.0, median - values_arr) / scale, 0.0, cap)


def robust_upper_bound(values: Sequence[float], multiplier: float = 3.0) -> float:
    """Upper population bound derived from the median and robust scale."""
    values_arr = np.asarray(values, dtype=np.float64)
    if values_arr.size == 0:
        raise ValueError("values cannot be empty")
    median, scale = _robust_scale(values_arr)
    return float(median + max(0.0, multiplier) * scale)


@dataclass
class UpdateAnalysis:
    """Auditable, server-computed evidence for one client update."""

    client_id: str
    update_norm: float
    distance_from_median: float
    cosine_similarity: float
    norm_robust_z: float
    distance_robust_z: float
    cosine_robust_z: float
    history_norm_robust_z: float
    deviation_score: float
    evidence_count: int
    status: str
    population_dispersion: float = 0.0
    adaptive_z_threshold: float = 3.5
    self_consistency: float = 0.0
    norm_ratio: float = 1.0
    extreme_evidence: bool = False
    action: str = "ACCEPTED"
    trust_score: float = 1.0
    aggregation_weight: float = 0.0
    reported_num_examples: int = 0
    effective_num_examples: float = 0.0
    rejection_reason: Optional[str] = None

    def to_dict(self) -> dict:
        value = asdict(self)
        for key, item in value.items():
            if isinstance(item, float):
                value[key] = round(item, 6)
        return value


class UpdateAnalyzer:
    """Analyze model *deltas* using robust, majority-relative statistics.

    Signals are calculated entirely at the server: distance from the
    coordinate-wise median delta, cosine agreement with that majority
    direction, round-relative update norm, and longitudinal norm behavior.
    """

    def __init__(
        self,
        robust_z_threshold: float = 3.5,
        suspicious_score: float = 0.35,
        malicious_score: float = 0.75,
        min_clients: int = 3,
        signal_weights: Optional[Mapping[str, float]] = None,
        heterogeneity_tolerance: float = 0.75,
        heterogeneous_population_threshold: float = 0.35,
        suspicious_min_evidence: int = 2,
        extreme_z_threshold: float = 8.0,
        extreme_norm_ratio: float = 5.0,
        opposing_cosine_threshold: float = -0.10,
    ):
        self.robust_z_threshold = float(robust_z_threshold)
        self.suspicious_score = float(suspicious_score)
        self.malicious_score = float(malicious_score)
        self.min_clients = int(min_clients)
        self.heterogeneity_tolerance = max(0.0, float(heterogeneity_tolerance))
        self.heterogeneous_population_threshold = max(
            0.0, float(heterogeneous_population_threshold)
        )
        self.suspicious_min_evidence = max(1, int(suspicious_min_evidence))
        self.extreme_z_threshold = max(
            self.robust_z_threshold, float(extreme_z_threshold)
        )
        self.extreme_norm_ratio = max(1.0, float(extreme_norm_ratio))
        self.opposing_cosine_threshold = float(opposing_cosine_threshold)
        supplied = dict(signal_weights or {})
        self.signal_weights = {
            "distance": float(supplied.get("distance", 0.35)),
            "cosine": float(supplied.get("cosine", 0.30)),
            "norm": float(supplied.get("norm", 0.25)),
            "history": float(supplied.get("history", 0.10)),
        }
        total = sum(max(0.0, value) for value in self.signal_weights.values())
        if total <= 0.0:
            raise ValueError("At least one update-analysis signal weight must be positive")
        self.signal_weights = {
            key: max(0.0, value) / total for key, value in self.signal_weights.items()
        }

    @staticmethod
    def _flatten_delta(weights: List[np.ndarray], baseline: List[np.ndarray]) -> np.ndarray:
        if len(weights) != len(baseline):
            raise ValueError("Client and baseline model structures differ")
        pieces = []
        for weight, base in zip(weights, baseline):
            if weight.shape != base.shape:
                raise ValueError("Client and baseline parameter shapes differ")
            delta = np.asarray(weight, dtype=np.float64) - np.asarray(base, dtype=np.float64)
            if not np.all(np.isfinite(delta)):
                raise ValueError("Client update contains NaN or infinite values")
            pieces.append(delta.reshape(-1))
        return np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float64)

    def analyze(
        self,
        client_ids: Sequence[str],
        client_weights: List[List[np.ndarray]],
        global_weights: List[np.ndarray],
        historical_norms: Optional[Mapping[str, Sequence[float]]] = None,
    ) -> List[UpdateAnalysis]:
        if len(client_ids) != len(client_weights):
            raise ValueError("client_ids and client_weights must have equal length")
        if not client_weights:
            return []

        deltas = []
        valid_client_ids = []
        valid_original_indices = []
        analyses_by_index: Dict[int, UpdateAnalysis] = {}
        for index, (client_id, weights) in enumerate(zip(client_ids, client_weights)):
            try:
                delta = self._flatten_delta(weights, global_weights)
            except (TypeError, ValueError, FloatingPointError) as exc:
                analyses_by_index[index] = UpdateAnalysis(
                    client_id=str(client_id),
                    update_norm=0.0,
                    distance_from_median=0.0,
                    cosine_similarity=-1.0,
                    norm_robust_z=20.0,
                    distance_robust_z=20.0,
                    cosine_robust_z=20.0,
                    history_norm_robust_z=0.0,
                    deviation_score=1.0,
                    evidence_count=4,
                    status="MALICIOUS",
                    rejection_reason=f"invalid update: {exc}",
                )
                continue
            deltas.append(delta)
            valid_client_ids.append(str(client_id))
            valid_original_indices.append(index)

        if not deltas:
            return [analyses_by_index[index] for index in range(len(client_ids))]
        if any(delta.shape != deltas[0].shape for delta in deltas):
            raise ValueError("All client updates must share the same parameter structure")
        matrix = np.stack(deltas, axis=0)
        majority = np.median(matrix, axis=0)
        majority_norm = float(np.linalg.norm(majority))
        norms = np.linalg.norm(matrix, axis=1)
        norm_reference = max(float(np.median(norms)), majority_norm, 1e-12)
        distances = np.linalg.norm(matrix - majority, axis=1) / norm_reference

        cosines = []
        for delta, update_norm in zip(deltas, norms):
            denom = float(update_norm) * majority_norm
            if denom <= 1e-12:
                cosines.append(1.0 if float(update_norm) <= 1e-12 and majority_norm <= 1e-12 else 0.0)
            else:
                cosines.append(float(np.clip(np.dot(delta, majority) / denom, -1.0, 1.0)))

        enough_clients = len(deltas) >= self.min_clients
        norm_z = _robust_upper_z(norms) if enough_clients else np.zeros(len(norms))
        distance_z = _robust_upper_z(distances) if enough_clients else np.zeros(len(norms))
        cosine_z = _robust_lower_z(cosines) if enough_clients else np.zeros(len(norms))
        distance_median, distance_scale = _robust_scale(distances)
        # A broad honest population is expected under non-IID RF exposure.  The
        # per-round robust threshold therefore expands with measured population
        # dispersion, while the separate extreme/opposing-direction checks stay
        # strict enough to reject high-amplitude sign-flip poisoning.
        population_dispersion = float(
            np.clip(distance_median + distance_scale, 0.0, 2.0)
        )
        adaptive_z_threshold = self.robust_z_threshold * (
            1.0 + self.heterogeneity_tolerance * population_dispersion
        )
        historical_norms = historical_norms or {}

        for idx, (client_id, original_index) in enumerate(zip(valid_client_ids, valid_original_indices)):
            history = np.asarray(historical_norms.get(client_id, []), dtype=np.float64)
            history_z = 0.0
            self_consistency = 0.0
            if history.size >= 3:
                median, scale = _robust_scale(history)
                history_z = float(np.clip(max(0.0, float(norms[idx]) - median) / scale, 0.0, 20.0))
                relative_change = abs(float(norms[idx]) - median) / max(abs(median), scale, 1e-12)
                self_consistency = float(np.exp(-relative_change))

            z_values = {
                "distance": float(distance_z[idx]),
                "cosine": float(cosine_z[idx]),
                "norm": float(norm_z[idx]),
                "history": history_z,
            }
            severities = {
                key: min(1.0, value / max(adaptive_z_threshold, 1e-12))
                for key, value in z_values.items()
            }
            deviation = float(sum(self.signal_weights[key] * severities[key] for key in severities))
            # Consistent longitudinal behavior is counter-evidence, not proof of
            # innocence.  It can reduce a population-only warning but never
            # suppress opposing-direction or extreme-magnitude evidence.
            # Mildly negative directions are legitimate when label/RF skew has
            # already made the honest population broad.  Tighten the opposing-
            # direction test in proportion to that measured dispersion; a true
            # sign-flip remains strongly negative and/or extreme in magnitude.
            adaptive_opposing_threshold = max(
                -0.75,
                self.opposing_cosine_threshold - 0.40 * population_dispersion,
            )
            opposing = float(cosines[idx]) <= adaptive_opposing_threshold
            norm_ratio = float(norms[idx]) / max(float(np.median(norms)), 1e-12)
            extreme_evidence = bool(
                norm_ratio >= max(self.extreme_norm_ratio, 8.0)
            )
            if self_consistency > 0.0 and not opposing and not extreme_evidence:
                deviation *= 1.0 - 0.25 * self_consistency
            evidence_count = sum(value >= adaptive_z_threshold for value in z_values.values())

            heterogeneous_population = (
                population_dispersion >= self.heterogeneous_population_threshold
            )
            corroborated_warning = (
                evidence_count >= self.suspicious_min_evidence
                or opposing
                or history_z >= adaptive_z_threshold
            )
            score_warning = deviation >= self.suspicious_score and (
                not heterogeneous_population or corroborated_warning
            )

            if enough_clients and extreme_evidence:
                status = "MALICIOUS"
            elif enough_clients and (corroborated_warning or score_warning):
                status = "SUSPICIOUS"
            else:
                status = "NORMAL"

            analyses_by_index[original_index] = UpdateAnalysis(
                client_id=str(client_id),
                update_norm=float(norms[idx]),
                distance_from_median=float(distances[idx]),
                cosine_similarity=float(cosines[idx]),
                norm_robust_z=float(norm_z[idx]),
                distance_robust_z=float(distance_z[idx]),
                cosine_robust_z=float(cosine_z[idx]),
                history_norm_robust_z=history_z,
                deviation_score=max(0.0, min(1.0, deviation)),
                evidence_count=evidence_count,
                status=status,
                population_dispersion=population_dispersion,
                adaptive_z_threshold=float(adaptive_z_threshold),
                self_consistency=self_consistency,
                norm_ratio=norm_ratio,
                extreme_evidence=extreme_evidence,
            )
        return [analyses_by_index[index] for index in range(len(client_ids))]


class TrustManager:
    """Maintain longitudinal trust and turn analysis into aggregation actions."""

    def __init__(
        self,
        history_alpha: float = 0.70,
        initial_trust: float = 0.80,
        suspicious_weight: float = 0.25,
        reject_trust_threshold: float = 0.15,
        suspicious_trust_threshold: float = 0.50,
        quarantine_rounds: int = 3,
        quarantine_trigger_rounds: int = 2,
        history_size: int = 20,
        state_path: Optional[str] = None,
    ):
        if not 0.0 <= history_alpha < 1.0:
            raise ValueError("history_alpha must be in [0, 1)")
        self.history_alpha = float(history_alpha)
        self.initial_trust = float(np.clip(initial_trust, 0.0, 1.0))
        self.suspicious_weight = float(np.clip(suspicious_weight, 0.0, 1.0))
        self.reject_trust_threshold = float(np.clip(reject_trust_threshold, 0.0, 1.0))
        self.suspicious_trust_threshold = float(np.clip(suspicious_trust_threshold, 0.0, 1.0))
        self.quarantine_rounds = max(0, int(quarantine_rounds))
        self.quarantine_trigger_rounds = max(1, int(quarantine_trigger_rounds))
        self.history_size = max(3, int(history_size))
        self._trust: Dict[str, float] = {}
        self._norm_history: Dict[str, deque] = {}
        self._deviation_history: Dict[str, deque] = {}
        self._low_trust_streak: Dict[str, int] = defaultdict(int)
        self._quarantine_remaining: Dict[str, int] = defaultdict(int)
        self._rounds: Dict[str, int] = defaultdict(int)
        self._total_detected = 0
        self._total_rejected = 0
        self._state_path = Path(state_path) if state_path else None
        self._load_state()

    def _load_state(self) -> None:
        """Restore reputation state without trusting malformed persisted data."""
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if payload.get("version") != 1:
                raise ValueError("unsupported trust-state version")
            clients = payload.get("clients", {})
            if not isinstance(clients, dict):
                raise ValueError("clients must be an object")
            for client_id, state in clients.items():
                if not isinstance(client_id, str) or not isinstance(state, dict):
                    raise ValueError("invalid client trust record")
                trust = float(state.get("trust_score", self.initial_trust))
                if not np.isfinite(trust) or not 0.0 <= trust <= 1.0:
                    raise ValueError("trust score outside [0, 1]")
                norms = [float(value) for value in state.get("norm_history", [])]
                deviations = [float(value) for value in state.get("deviation_history", [])]
                if not all(np.isfinite(value) and value >= 0.0 for value in norms):
                    raise ValueError("invalid norm history")
                if not all(np.isfinite(value) and 0.0 <= value <= 1.0 for value in deviations):
                    raise ValueError("invalid deviation history")
                self._trust[client_id] = trust
                self._norm_history[client_id] = deque(norms[-self.history_size:], maxlen=self.history_size)
                self._deviation_history[client_id] = deque(
                    deviations[-self.history_size:], maxlen=self.history_size
                )
                self._low_trust_streak[client_id] = max(0, int(state.get("low_trust_streak", 0)))
                self._quarantine_remaining[client_id] = max(
                    0, int(state.get("quarantine_remaining", 0))
                )
                self._rounds[client_id] = max(0, int(state.get("rounds", 0)))
            counters = payload.get("counters", {})
            self._total_detected = max(0, int(counters.get("total_detected", 0)))
            self._total_rejected = max(0, int(counters.get("total_rejected", 0)))
            logger.info("[Trust] Restored reputation for %d clients from %s", len(clients), self._state_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("[Trust] Ignoring invalid persisted state %s: %s", self._state_path, exc)
            self._trust.clear()
            self._norm_history.clear()
            self._deviation_history.clear()
            self._low_trust_streak.clear()
            self._quarantine_remaining.clear()
            self._rounds.clear()
            self._total_detected = 0
            self._total_rejected = 0

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "version": 1,
            "clients": {
                client_id: {
                    "trust_score": self._trust[client_id],
                    "norm_history": list(self._norm_history.get(client_id, [])),
                    "deviation_history": list(self._deviation_history.get(client_id, [])),
                    "low_trust_streak": self._low_trust_streak[client_id],
                    "quarantine_remaining": self._quarantine_remaining[client_id],
                    "rounds": self._rounds[client_id],
                }
                for client_id in sorted(self._trust)
            },
            "counters": {
                "total_detected": self._total_detected,
                "total_rejected": self._total_rejected,
            },
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{self._state_path.name}.",
                suffix=".tmp",
                dir=self._state_path.parent,
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, self._state_path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def historical_norms(self) -> Dict[str, List[float]]:
        return {client_id: list(values) for client_id, values in self._norm_history.items()}

    def get_trust(self, client_id: str) -> float:
        return self._trust.get(client_id, self.initial_trust)

    def get(self, client_id: str):
        """Compatibility view used by the optional client selector."""
        return SimpleNamespace(trust_score=self.get_trust(client_id))

    def calculate_trust_score(self, client_id: str, analysis: UpdateAnalysis) -> float:
        """Calculate bounded EMA trust from server-observed update quality."""
        previous = self.get_trust(client_id)
        quality = 1.0 - analysis.deviation_score
        if analysis.status == "MALICIOUS":
            quality = 0.0
        elif analysis.status == "SUSPICIOUS":
            quality *= self.suspicious_weight
        trust = self.history_alpha * previous + (1.0 - self.history_alpha) * quality
        return float(np.clip(trust, 0.0, 1.0))

    def update_round(self, analyses: List[UpdateAnalysis]) -> List[UpdateAnalysis]:
        for analysis in analyses:
            client_id = analysis.client_id
            remaining = self._quarantine_remaining[client_id]
            if remaining > 0:
                self._quarantine_remaining[client_id] = remaining - 1

            trust = self.calculate_trust_score(client_id, analysis)
            self._trust[client_id] = trust
            self._rounds[client_id] += 1
            self._norm_history.setdefault(client_id, deque(maxlen=self.history_size)).append(analysis.update_norm)
            self._deviation_history.setdefault(
                client_id, deque(maxlen=self.history_size)
            ).append(analysis.deviation_score)
            analysis.trust_score = trust

            if analysis.status != "NORMAL":
                self._total_detected += 1
            # A single robust-statistic outlier is only down-weighted. Quarantine
            # requires either current multi-signal malicious evidence or trust
            # that has decayed through repeated suspicious rounds all the way to
            # the explicit rejection threshold. This avoids turning ordinary
            # non-IID client drift into a two-round automatic ban.
            if analysis.status == "MALICIOUS" or trust <= self.reject_trust_threshold:
                self._low_trust_streak[client_id] += 1
            else:
                self._low_trust_streak[client_id] = 0
            if self._low_trust_streak[client_id] >= self.quarantine_trigger_rounds:
                self._quarantine_remaining[client_id] = self.quarantine_rounds
                self._low_trust_streak[client_id] = 0

            quarantined = self._quarantine_remaining[client_id] > 0
            if analysis.status == "MALICIOUS":
                analysis.action = "REJECTED"
                analysis.rejection_reason = (
                    analysis.rejection_reason or "multiple robust outlier signals"
                )
            elif quarantined or trust <= self.reject_trust_threshold:
                analysis.action = "REJECTED"
                analysis.rejection_reason = "historical trust quarantine"
            elif analysis.status == "SUSPICIOUS" or trust < self.suspicious_trust_threshold:
                analysis.action = "DOWN_WEIGHTED"
            else:
                analysis.action = "ACCEPTED"
            if analysis.action == "REJECTED":
                self._total_rejected += 1
        self._save_state()
        return analyses

    def snapshot(self) -> List[dict]:
        rows = []
        for client_id in sorted(self._trust):
            deviations: list[float] = list(self._deviation_history.get(client_id, []))
            rows.append({
                "client_id": client_id,
                "trust_score": round(self._trust[client_id], 6),
                "rounds_participated": self._rounds[client_id],
                "quarantine_remaining": self._quarantine_remaining[client_id],
                "is_quarantined": self._quarantine_remaining[client_id] > 0,
                "mean_recent_deviation": round(float(np.mean(deviations)), 6) if deviations else 0.0,
            })
        return rows

    def summary(self) -> dict:
        values = list(self._trust.values())
        quarantined = sum(value > 0 for value in self._quarantine_remaining.values())
        return {
            "n_clients": len(values),
            "mean_trust": round(float(np.mean(values)), 6) if values else 1.0,
            "min_trust": round(float(np.min(values)), 6) if values else 1.0,
            "n_quarantined": quarantined,
            "quarantine_rate": round(quarantined / len(values), 6) if values else 0.0,
            "total_poisoning_attempts_detected": self._total_detected,
            "total_updates_rejected": self._total_rejected,
        }


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
    Legacy reported-metric trust registry for optional client selection.

    This class is not used by ``SecureFedAvgV2`` and is not a Byzantine
    security boundary. The live aggregation path uses ``TrustManager`` above.

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
    aggregated: list[np.ndarray] = []
    for layer_idx in range(n_layers):
        weighted: np.ndarray = sum(  # type: ignore[assignment]
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
