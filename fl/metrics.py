"""
fl/metrics.py — Centralized FL Metrics Tracker  [FLARE v2]

Aggregates, tracks, and exports all evaluation signals from the upgraded
FL pipeline. Writes to both CSV (append per round) and JSON (snapshot).

Tracked Metrics:
  Core FL:
    - threat_f1         : Macro-F1 for jammed/not-jammed classification
    - attack_accuracy   : Attack type classification accuracy
    - round_loss        : Mean training loss across clients

  Privacy:
    - privacy_epsilon   : Cumulative (ε, δ)-DP budget consumed
    - privacy_delta     : Fixed δ for DP guarantee

  Compression:
    - compression_ratio : Mean ratio across clients (original/compressed bytes)
    - bytes_saved       : Total bytes saved vs uncompressed

  Trust:
    - mean_trust        : Mean client trust score τ̄
    - min_trust         : Minimum trust score this round
    - quarantine_rate   : Fraction of clients quarantined
    - n_quarantined     : Number of quarantined clients

  Client Selection:
    - jains_fairness    : Jain's Fairness Index J(x)
    - selection_entropy : Shannon entropy of selection distribution

  Personalization:
    - mean_persona_delta     : Mean L2 distance between personal and global models
    - mean_personal_loss     : Mean personalized training loss

  Drift Detection:
    - drift_rate        : Fraction of clients with drift detected this round
    - mean_drift_score  : Mean combined drift score across clients
    - total_drift_events: Cumulative drift events

  Knowledge Distillation:
    - kd_loss           : KD loss (when distillation ran)
    - kd_accuracy_gain  : Accuracy improvement from KD (when measured)

  Async FL:
    - async_buffer_fill : Buffer fill level at aggregation
    - total_aggregations: Total async aggregation steps

  Convergence:
    - convergence_round : First round achieving target F1 (or None)

All exported to:
  - results/fl_advanced_metrics.csv  (one row per round, append mode)
  - results/fl_metrics_snapshot.json (overwritten each round — latest state)

Unit-test hooks: run `python -m fl.metrics` for self-test.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# CSV column order (deterministic for analysis)
_CSV_COLUMNS = [
    # Round metadata
    "round", "timestamp", "wall_time_s",
    # Core FL
    "threat_f1", "attack_accuracy", "round_loss", "clients_used", "clients_excluded",
    # Privacy
    "privacy_epsilon", "privacy_delta",
    # Compression
    "compression_ratio", "bytes_saved",
    # Trust
    "mean_trust", "min_trust", "quarantine_rate", "n_quarantined",
    # Selection
    "jains_fairness", "selection_entropy",
    # Personalization
    "mean_persona_delta", "mean_personal_loss",
    # Drift
    "drift_rate", "mean_drift_score", "total_drift_events",
    # KD
    "kd_loss", "kd_accuracy_gain",
    # Async
    "async_buffer_fill", "total_aggregations",
    # Convergence
    "convergence_round",
    # Aggregation method
    "aggregation_method",
]


# ---------------------------------------------------------------------------
# Metrics Tracker
# ---------------------------------------------------------------------------

class FLMetricsTracker:
    """
    Central metrics accumulator for the FL server.

    Usage (in SecureFedAvg.aggregate_fit()):
        tracker = FLMetricsTracker(csv_path=..., json_path=...)
        tracker.update("threat_f1", 0.87)
        tracker.update("privacy_epsilon", 3.14)
        tracker.commit(round_num=5)  # writes CSV row + JSON snapshot
    """

    def __init__(
        self,
        csv_path: Optional[str] = None,
        json_path: Optional[str] = None,
        convergence_target_f1: float = 0.90,
    ):
        base = Path(__file__).parent.parent
        self._csv_path = Path(csv_path) if csv_path else base / "results" / "fl_advanced_metrics.csv"
        self._json_path = Path(json_path) if json_path else base / "results" / "fl_metrics_snapshot.json"
        self._convergence_target = convergence_target_f1

        os.makedirs(self._csv_path.parent, exist_ok=True)
        os.makedirs(self._json_path.parent, exist_ok=True)

        self._current: Dict[str, Any] = self._empty_row()
        self._history: List[Dict[str, Any]] = []
        self._start_time: float = time.time()
        self._round_start: float = time.time()
        self._convergence_round: Optional[int] = None
        self._csv_initialized: bool = False

    def _empty_row(self) -> Dict[str, Any]:
        return {col: None for col in _CSV_COLUMNS}

    def start_round(self, round_num: int) -> None:
        """Reset accumulator for a new round."""
        self._current = self._empty_row()
        self._current["round"] = round_num
        self._current["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._round_start = time.time()

    def update(self, key: str, value: Any) -> None:
        """Update a single metric value."""
        if key not in self._current:
            logger.warning("[Metrics] Unknown metric key: %s (will be ignored in CSV)", key)
        self._current[key] = value

    def update_bulk(self, metrics: Dict[str, Any]) -> None:
        """Update multiple metrics at once from a dict."""
        for k, v in metrics.items():
            self.update(k, v)

    def update_privacy(self, accountant_summary: dict) -> None:
        """Extract privacy metrics from a PrivacyAccountant summary dict."""
        self.update("privacy_epsilon", accountant_summary.get("epsilon"))
        self.update("privacy_delta", accountant_summary.get("delta"))

    def update_trust(self, registry_summary: dict) -> None:
        """Extract trust metrics from a TrustRegistry summary dict."""
        self.update("mean_trust", registry_summary.get("mean_trust"))
        self.update("quarantine_rate", registry_summary.get("quarantine_rate"))
        self.update("n_quarantined", registry_summary.get("n_quarantined"))

    def update_compression(self, stats_list: List[dict]) -> None:
        """Compute mean compression stats across clients."""
        if not stats_list:
            return
        ratios = [s.get("compression_ratio", 1.0) for s in stats_list if s]
        saved = [s.get("original_bytes", 0) - s.get("compressed_bytes", 0)
                 for s in stats_list if s]
        if ratios:
            self.update("compression_ratio", round(sum(ratios) / len(ratios), 2))
        if saved:
            self.update("bytes_saved", sum(saved))

    def update_drift(self, drift_audits: List[dict]) -> None:
        """Compute drift statistics from per-client audit dicts."""
        if not drift_audits:
            return
        drifted = sum(1 for d in drift_audits if d.get("drift_detected"))
        drift_scores = [d.get("drift_score", 0.0) for d in drift_audits]
        total_events = sum(d.get("total_drift_events", 0) for d in drift_audits)

        self.update("drift_rate", round(drifted / len(drift_audits), 4))
        self.update("mean_drift_score", round(sum(drift_scores) / len(drift_scores), 4))
        self.update("total_drift_events", total_events)

    def update_personalization(self, persona_metrics_list: List[dict]) -> None:
        """Compute mean personalization metrics across clients."""
        if not persona_metrics_list:
            return
        deltas = [m.get("persona_delta", 0.0) for m in persona_metrics_list if m]
        losses = [m.get("personal_loss", 0.0) for m in persona_metrics_list if m]
        if deltas:
            self.update("mean_persona_delta", round(sum(deltas) / len(deltas), 4))
        if losses:
            self.update("mean_personal_loss", round(sum(losses) / len(losses), 4))

    def update_selection(self, selector_summary: dict) -> None:
        """Extract selection fairness metrics."""
        self.update("jains_fairness", selector_summary.get("jains_fairness"))
        # Compute selection entropy from counts
        counts = selector_summary.get("selection_counts", {})
        if counts:
            import math
            total = sum(counts.values())
            if total > 0:
                probs = [v / total for v in counts.values() if v > 0]
                entropy = -sum(p * math.log2(p) for p in probs)
                self.update("selection_entropy", round(entropy, 4))

    def update_kd(self, kd_metrics: Optional[dict]) -> None:
        """Update KD metrics (None if distillation did not run this round)."""
        if kd_metrics is not None:
            self.update("kd_loss", kd_metrics.get("kd_loss"))

    def update_async(self, buffer_status: dict) -> None:
        """Update async FL buffer metrics."""
        self.update("async_buffer_fill", buffer_status.get("buffer_current"))
        self.update("total_aggregations", buffer_status.get("total_aggregations"))

    def update_min_trust(self, trust_snapshot: List[dict]) -> None:
        """Compute minimum trust across all clients."""
        if trust_snapshot:
            min_t = min(s.get("trust_score", 1.0) for s in trust_snapshot)
            self.update("min_trust", round(min_t, 4))

    def commit(self, round_num: int) -> Dict[str, Any]:
        """
        Finalize the current round's metrics, write to CSV and JSON.

        Returns the completed metrics row as a dict.
        """
        # Fill in wall time
        self._current["wall_time_s"] = round(time.time() - self._round_start, 2)
        self._current["round"] = round_num

        # Check convergence
        f1 = self._current.get("threat_f1")
        if f1 is not None and self._convergence_round is None and f1 >= self._convergence_target:
            self._convergence_round = round_num
            logger.info("[Metrics] Convergence achieved at round %d (F1=%.4f)", round_num, f1)
        self._current["convergence_round"] = self._convergence_round

        self._history.append(dict(self._current))
        self._write_csv_row(self._current)
        self._write_json_snapshot()

        logger.info(
            "[Metrics] Round %d | F1=%.4f | ε=%.4f | trust=%.4f | compress=%.2f× | drift=%.4f",
            round_num,
            self._current.get("threat_f1") or 0.0,
            self._current.get("privacy_epsilon") or 0.0,
            self._current.get("mean_trust") or 0.0,
            self._current.get("compression_ratio") or 1.0,
            self._current.get("mean_drift_score") or 0.0,
        )

        return dict(self._current)

    def _write_csv_row(self, row: Dict[str, Any]) -> None:
        """Append one row to the CSV file (write header on first call)."""
        write_header = not self._csv_initialized and not self._csv_path.exists()
        self._csv_initialized = True

        with open(self._csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in _CSV_COLUMNS})

    def _write_json_snapshot(self) -> None:
        """Write the full metrics history + summary to JSON."""
        summary = {
            "last_round": self._current.get("round"),
            "convergence_round": self._convergence_round,
            "n_rounds_completed": len(self._history),
            "latest": self._current,
            "history": self._history[-20:],  # last 20 rounds
        }
        with open(self._json_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)

    def get_history(self) -> List[Dict[str, Any]]:
        """Return full metrics history as list of dicts."""
        return list(self._history)

    def get_best_f1(self) -> float:
        """Return the best threat_f1 achieved across all rounds."""
        f1s = [r.get("threat_f1") for r in self._history if r.get("threat_f1") is not None]
        return max(f1s, default=0.0)

    def benchmark_summary(self) -> dict:
        """
        Generate a benchmark comparison summary:
        baseline (round 1) vs best vs latest.
        """
        if not self._history:
            return {}
        first = self._history[0]
        latest = self._history[-1]
        best_f1_round = max(
            self._history,
            key=lambda r: r.get("threat_f1") or 0.0,
            default={},
        )
        return {
            "baseline_f1": first.get("threat_f1"),
            "best_f1": best_f1_round.get("threat_f1"),
            "best_f1_round": best_f1_round.get("round"),
            "latest_f1": latest.get("threat_f1"),
            "convergence_round": self._convergence_round,
            "baseline_epsilon": first.get("privacy_epsilon"),
            "latest_epsilon": latest.get("privacy_epsilon"),
            "baseline_compression": first.get("compression_ratio"),
            "latest_compression": latest.get("compression_ratio"),
            "total_rounds": len(self._history),
        }


# ---------------------------------------------------------------------------
# Utility: Load and display metrics from CSV
# ---------------------------------------------------------------------------

def load_metrics_csv(path: Optional[str] = None) -> List[dict]:
    """Load metrics CSV into a list of dicts for analysis/plotting."""
    if path is None:
        path = str(Path(__file__).parent.parent / "results" / "fl_advanced_metrics.csv")
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        return list(reader)


def print_benchmark_table(history: List[dict]) -> None:
    """Print a formatted benchmark table to stdout."""
    if not history:
        print("No metrics history available.")
        return

    print(f"\n{'Round':>5} {'F1':>7} {'Atk Acc':>8} {'ε':>7} {'Trust':>7} {'Compress':>9} {'Drift':>7} {'Quarantine':>10}")
    print("-" * 75)
    for r in history:
        def fmt(v, fmt_str=".4f"):
            return f"{float(v):{fmt_str}}" if v not in (None, "") else "  N/A "
        print(
            f"{r.get('round','?'):>5} "
            f"{fmt(r.get('threat_f1')):>7} "
            f"{fmt(r.get('attack_accuracy')):>8} "
            f"{fmt(r.get('privacy_epsilon')):>7} "
            f"{fmt(r.get('mean_trust')):>7} "
            f"{fmt(r.get('compression_ratio'),'.2f'):>9} "
            f"{fmt(r.get('mean_drift_score')):>7} "
            f"{fmt(r.get('quarantine_rate')):>10}"
        )
    print()


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_tracker_roundtrip(tmp_path: Optional[str] = None):
    """Verify tracker writes and reads CSV correctly."""
    import tempfile
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()

    csv_p = str(Path(tmp_path) / "test_metrics.csv")
    json_p = str(Path(tmp_path) / "test_metrics.json")
    tracker = FLMetricsTracker(csv_path=csv_p, json_path=json_p)

    for r in range(1, 4):
        tracker.start_round(r)
        tracker.update("threat_f1", 0.70 + r * 0.05)
        tracker.update("privacy_epsilon", r * 1.2)
        tracker.update("mean_trust", 0.85)
        tracker.update("compression_ratio", 10.0)
        tracker.commit(r)

    rows = load_metrics_csv(csv_p)
    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    assert float(rows[0]["threat_f1"]) == 0.75, "Round 1 F1 mismatch"

    with open(json_p) as f:
        snapshot = json.load(f)
    assert snapshot["n_rounds_completed"] == 3

    print(f"[PASS] Metrics tracker: {len(rows)} rows written to CSV, JSON snapshot OK")


def _test_convergence_detection():
    """Verify convergence round is recorded correctly."""
    import tempfile
    tmp = tempfile.mkdtemp()
    tracker = FLMetricsTracker(
        csv_path=f"{tmp}/m.csv", json_path=f"{tmp}/m.json",
        convergence_target_f1=0.90,
    )
    for r in range(1, 6):
        tracker.start_round(r)
        tracker.update("threat_f1", 0.80 + r * 0.03)
        tracker.commit(r)

    assert tracker._convergence_round is not None, "Convergence should be recorded"
    print(f"[PASS] Convergence detected at round {tracker._convergence_round}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _test_tracker_roundtrip()
    _test_convergence_detection()
    print("All fl/metrics.py tests passed.")
