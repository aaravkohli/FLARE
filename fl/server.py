"""
fl/server.py — Upgraded FL Server  [FLARE v2]

Flower FL server integrating all FLARE v2 components:
  1. Optional client-selection metrics (PoCo + UCB utility is opt-in)
  2. Trust + Reputation Engine (EMA scoring, quarantine)
  3. Client Drift Detection (cosine, KS, loss spike)
  4. 5-Layer Secure Aggregation (clip + Z-score + trimmed mean + trust + DP)
  5. Knowledge Distillation (FedDF, every N rounds)
  6. Client-local personalization metrics
  7. Async FL Buffer (FedBuff emulation)
  8. Centralized Metrics Tracking (CSV + JSON)

All features are independently configurable via config/fl_config.yaml.
Backward-compatible with the original FLARE v1 SecureFedAvg interface.

Usage:
  python fl/server.py [--rounds 20] [--no-async] [--no-kd]

References:
  McMahan et al. (2017) — FedAvg
  Lai et al. (2021) — Power-of-Choice (Oort)
  Cao et al. (2020) — FLTrust
  Lin et al. (2020) — FedDF
  Nguyen et al. (2022) — FedBuff
  T. Dinh et al. (2020) — pFedMe
"""

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).parent.parent))

import flwr as fl
import numpy as np
import torch
import yaml
from flwr.common import (
    FitRes,
    Parameters,
    Scalar,
    ndarrays_to_parameters,
    parameters_to_ndarrays,
)
from flwr.server.client_proxy import ClientProxy

from fl.aggregator import secure_aggregate
from fl.checkpoint import write_fl_checkpoint_metadata
from fl.data import build_temporal_windows
from fl.model import build_model, set_model_weights, get_model_weights

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
_FL_CFG = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_MODEL_CFG = _FL_CFG["model"]
_FED_CFG = _FL_CFG["federation"]
_SEC_CFG = _FL_CFG["security"]
_PATHS_CFG = _FL_CFG.get("paths", {})

# New v2 config sections
_DP_CFG = _FL_CFG.get("differential_privacy", {})
_TRUST_CFG = _FL_CFG.get("trust", {})
_DRIFT_CFG_V2 = _FL_CFG.get("drift_detection", {})
_SEL_CFG = _FL_CFG.get("client_selection", {})
_ASYNC_CFG = _FL_CFG.get("async_fl", {})
_PERSONA_CFG = _FL_CFG.get("personalization", {})
_KD_CFG = _FL_CFG.get("distillation", {})
_DATA_CFG = _FL_CFG.get("data", {})

_PROC = _BASE / "datasets" / "processed"
_RESULTS = _BASE / "results"
os.makedirs(_BASE / "models", exist_ok=True)
os.makedirs(_RESULTS, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVER] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upgraded Strategy: SecureFedAvgV2
# ---------------------------------------------------------------------------

class SecureFedAvgV2(fl.server.strategy.FedAvg):
    """
    FLARE v2 Federated Averaging Strategy.

    Extends Flower's FedAvg with the full FLARE v2 pipeline:
      - Optional client-selection metrics (PoCo + UCB utility)
      - Trust scoring and quarantine
      - Multi-signal drift detection
      - 5-layer secure aggregation
      - FedDF knowledge distillation (periodic)
      - Client-local personalization metrics
      - FedBuff async buffer (if async mode enabled)
      - Centralized metrics tracking

    All components are independently enabled/disabled via config flags.
    """

    def __init__(self, initial_parameters: Parameters, **kwargs):
        super().__init__(initial_parameters=initial_parameters, **kwargs)
        self._global_weights: Optional[List[np.ndarray]] = parameters_to_ndarrays(
            initial_parameters
        )

        # Initialize v2 components (guarded by feature flags)
        self._init_trust()
        self._init_drift()
        self._init_selection()
        self._init_async()
        self._init_distillation()
        self._init_metrics()

        logger.info(
            "[Server v2] Initialized | trust=%s | drift=%s | selection=%s | "
            "async=%s | kd=%s | persona=%s",
            _TRUST_CFG.get("enabled", False),
            _DRIFT_CFG_V2.get("enabled", True),
            _SEL_CFG.get("enabled", False),
            _ASYNC_CFG.get("enabled", False),
            _KD_CFG.get("enabled", False),
            _PERSONA_CFG.get("enabled", False),
        )

    def _init_trust(self):
        if _TRUST_CFG.get("enabled", False):
            from fl.trust import TrustRegistry
            self._trust = TrustRegistry(
                alpha=_TRUST_CFG.get("alpha", 0.9),
                tau_min=_TRUST_CFG.get("tau_min", 0.2),
                quarantine_rounds=_TRUST_CFG.get("quarantine_rounds", 3),
                initial_trust=_TRUST_CFG.get("initial_trust", 1.0),
                participation_bonus=_TRUST_CFG.get("participation_bonus", 0.05),
            )
        else:
            self._trust = None

    def _init_drift(self):
        if _DRIFT_CFG_V2.get("enabled", True):
            from fl.drift import DriftRegistry
            weights = _DRIFT_CFG_V2.get("weights", {})
            self._drift = DriftRegistry(
                cosine_threshold=_DRIFT_CFG_V2.get("cosine", {}).get("threshold", 0.3),
                ks_p_threshold=_DRIFT_CFG_V2.get("ks_p_threshold", 0.05),
                loss_spike_threshold=_DRIFT_CFG_V2.get("loss_spike", {}).get("ratio_threshold", 2.0),
                weights=weights,
                combined_threshold=_DRIFT_CFG_V2.get("combined_threshold", 0.5),
                enabled=True,
            )
        else:
            self._drift = None

    def _init_selection(self):
        if _SEL_CFG.get("enabled", False):
            from fl.selection import AdaptiveClientSelector
            poco_cfg = _SEL_CFG.get("poco", {})
            ucb_cfg = _SEL_CFG.get("ucb", {})
            combined_cfg = _SEL_CFG.get("combined", {})
            self._selector = AdaptiveClientSelector(
                strategy=_SEL_CFG.get("strategy", "combined"),
                candidate_multiplier=poco_cfg.get("candidate_multiplier", 2),
                ucb_beta=ucb_cfg.get("beta", 0.5),
                poco_weight=combined_cfg.get("poco_weight", 0.6),
                ucb_weight=combined_cfg.get("ucb_weight", 0.4),
                trust_registry=self._trust,
                enabled=True,
            )
        else:
            self._selector = None

    def _init_async(self):
        if _ASYNC_CFG.get("enabled", False):
            from fl.async_fl import AsyncFLBuffer, ConvergenceTracker
            self._async_buffer = AsyncFLBuffer(
                buffer_size=_ASYNC_CFG.get("buffer_size", 4),
                max_staleness=_ASYNC_CFG.get("max_staleness", 10),
                staleness_alpha=_ASYNC_CFG.get("staleness_alpha", 1.0),
                enabled=True,
            )
            self._convergence_tracker = ConvergenceTracker(target_f1=0.90)
        else:
            self._async_buffer = None
            self._convergence_tracker = None

    def _init_distillation(self):
        if _KD_CFG.get("enabled", False):
            from fl.distillation import DistillationController
            self._distillation = DistillationController(
                enabled=True,
                temperature=_KD_CFG.get("temperature", 3.0),
                kd_epochs=_KD_CFG.get("kd_epochs", 5),
                kd_every_n_rounds=_KD_CFG.get("kd_every_n_rounds", 10),
                proxy_samples=_KD_CFG.get("proxy_samples", 500),
                kd_lr=_KD_CFG.get("kd_lr", 0.001),
                alpha_kd=_KD_CFG.get("alpha_kd", 0.7),
                seq_len=_MODEL_CFG["sequence_len"],
                n_features=_MODEL_CFG["input_features"],
            )
        else:
            self._distillation = None

    def _init_metrics(self):
        from fl.metrics import FLMetricsTracker
        metrics_csv = str(_BASE / _PATHS_CFG.get("metrics_csv", "results/fl_advanced_metrics.csv"))
        metrics_json = str(_BASE / _PATHS_CFG.get("metrics_json", "results/fl_metrics_snapshot.json"))
        self._metrics = FLMetricsTracker(
            csv_path=metrics_csv,
            json_path=metrics_json,
            convergence_target_f1=0.90,
        )

    # -----------------------------------------------------------------------
    # Flower: aggregate_fit — main round aggregation
    # -----------------------------------------------------------------------

    def aggregate_fit(
        self,
        server_round: int,
        results: List[Tuple[ClientProxy, FitRes]],
        failures: List[Union[Tuple[ClientProxy, FitRes], BaseException]],
    ) -> Tuple[Optional[Parameters], Dict[str, Scalar]]:
        """
        FLARE v2 aggregation pipeline:
          1. Extract client updates and metrics
          2. Trust update (register + score each client)
          3. Drift detection per client
          4. Quarantine filtering
          5. Secure aggregation (5-layer)
          6. Record client-local personalization metrics
          7. Knowledge distillation (if scheduled)
          8. Save model
          9. Evaluate on test set
          10. Update metrics tracker
        """
        self._metrics.start_round(server_round)

        if not results:
            logger.warning("Round %d: no client results received.", server_round)
            return None, {}

        # Step 1: Extract client data
        client_weights: List[List[np.ndarray]] = []
        num_examples: List[int] = []
        client_ids: List[str] = []
        client_losses: List[float] = []
        client_metrics_list: List[dict] = []
        compression_stats_list: List[dict] = []
        persona_metrics_list: List[dict] = []
        drift_audits: List[dict] = []

        for _, fit_res in results:
            w = parameters_to_ndarrays(fit_res.parameters)
            client_weights.append(w)
            num_examples.append(fit_res.num_examples)
            m = fit_res.metrics or {}
            client_ids.append(m.get("client_id", f"unknown_{len(client_ids)}"))
            client_losses.append(float(m.get("train_loss", 0.0)))
            client_metrics_list.append(m)
            compression_stats_list.append({
                "compression_ratio": m.get("compression_ratio", 1.0),
                "original_bytes": m.get("original_bytes", 0),
                "compressed_bytes": m.get("compressed_bytes", 0),
            })
            persona_metrics_list.append({
                "persona_delta": m.get("persona_delta", 0.0),
                "personal_loss": m.get("personal_loss", 0.0),
            })

        private_metrics = [
            m for m in client_metrics_list if int(m.get("dp_enabled", 0)) == 1
        ]
        if private_metrics:
            self._metrics.update_privacy({
                "epsilon": max(float(m.get("dp_epsilon", 0.0)) for m in private_metrics),
                "delta": max(float(m.get("dp_delta", 0.0)) for m in private_metrics),
            })

        # Step 2: Trust update
        agg_weights: Optional[List[float]] = None
        trust_snapshot = []
        if self._trust is not None:
            # Compute anomaly scores for quality signal
            from fl.aggregator import compute_anomaly_scores
            temp_scores = compute_anomaly_scores(client_weights)
            for cid, w, score, m in zip(client_ids, client_weights, temp_scores, client_metrics_list):
                val_acc = m.get("accuracy")
                self._trust.update(
                    client_id=cid,
                    anomaly_score=score,
                    val_accuracy=float(val_acc) if val_acc is not None else None,
                    val_loss=m.get("train_loss"),
                    participated=True,
                )
                if self._selector is not None:
                    self._selector.update_client_stats(cid, loss=m.get("train_loss"))

            self._trust.decrement_all_quarantines()
            trust_snapshot = self._trust.snapshot()
            trust_summary = self._trust.summary()
            self._metrics.update_trust(trust_summary)
            self._metrics.update_min_trust(trust_snapshot)

            # Compute trust-weighted aggregation weights
            agg_weights = self._trust.get_aggregation_weights(client_ids, num_examples)
            logger.info(
                "[Trust] Round %d | mean_τ=%.4f | quarantine_rate=%.2f",
                server_round, trust_summary["mean_trust"], trust_summary["quarantine_rate"],
            )

        # Step 3: Drift detection
        if self._drift is not None:
            for cid, w, m in zip(client_ids, client_weights, client_metrics_list):
                rssi_json = m.get("rssi_values_json")
                rssi_vals = np.array(json.loads(rssi_json)) if rssi_json else None
                audit = self._drift.update(
                    client_id=cid,
                    curr_weights=w,
                    train_loss=m.get("train_loss"),
                    rssi_values=rssi_vals,
                )
                drift_audits.append(audit)
            self._metrics.update_drift(drift_audits)

        # Step 4: Quarantine filtering (remove quarantined clients from aggregation)
        if self._trust is not None:
            eligible_ids = self._trust.get_eligible_clients(client_ids)
            eligible_mask = [cid in eligible_ids for cid in client_ids]
            client_weights = [w for w, m in zip(client_weights, eligible_mask) if m]
            num_examples = [n for n, m in zip(num_examples, eligible_mask) if m]
            agg_weights = [w for w, m in zip(agg_weights, eligible_mask) if m] if agg_weights else None
            client_ids = [c for c, m in zip(client_ids, eligible_mask) if m]

        if not client_weights:
            logger.error("All clients quarantined or excluded. Skipping aggregation.")
            return None, {}

        # Step 5: Use current global weights as baseline for clipping
        baseline = self._global_weights
        if baseline is None:
            raise RuntimeError("Global aggregation baseline is not initialized")

        # Server-side DP config
        server_dp_cfg = _DP_CFG.get("server_side", {})
        server_dp_scale = (
            server_dp_cfg.get("noise_scale", 0.01)
            if _DP_CFG.get("enabled", False) and server_dp_cfg.get("enabled", True)
            else 0.0
        )

        # 5-layer secure aggregation
        aggregated, audit = secure_aggregate(
            client_weights=client_weights,
            global_weights=baseline,
            num_examples=num_examples,
            clip_norm=_SEC_CFG["clip_norm"],
            z_threshold=_SEC_CFG["anomaly_z_threshold"],
            use_trimmed_mean=_SEC_CFG["use_trimmed_mean"],
            trim_ratio=_SEC_CFG["trim_ratio"],
            agg_weights=agg_weights,           # Layer 4 (NEW v2)
            server_dp_noise_scale=server_dp_scale,  # Layer 5 (NEW v2)
            server_dp_sensitivity=_SEC_CFG["clip_norm"],
        )

        self._global_weights = aggregated

        # Step 6: client-local personalization metrics
        if _PERSONA_CFG.get("enabled", False) and persona_metrics_list:
            # Personal models remain client-local. They are measured here but are
            # intentionally not substituted for the federated update.
            logger.debug("Client-local personalization metrics recorded for round %d", server_round)

        # Step 7: Knowledge Distillation (FedDF)
        kd_metrics = None
        if self._distillation is not None:
            global_model = build_model(_MODEL_CFG)
            set_model_weights(global_model, aggregated)
            client_models = []
            for w in client_weights[:6]:  # cap at 6 teachers for memory
                m = build_model(_MODEL_CFG)
                set_model_weights(m, w)
                client_models.append(m)

            kd_metrics = self._distillation.maybe_distill(
                round_num=server_round,
                global_model=global_model,
                client_models=client_models,
            )
            if kd_metrics is not None:
                # Use distilled model as aggregated result
                aggregated = get_model_weights(global_model)
                self._global_weights = aggregated
                logger.info("[KD] Distillation applied: kd_loss=%.4f", kd_metrics.get("kd_loss", 0))

        self._metrics.update_kd(kd_metrics)

        # Metrics: compression
        self._metrics.update_compression(compression_stats_list)
        self._metrics.update_personalization(persona_metrics_list)

        # Async FL buffer update
        if self._async_buffer is not None:
            self._async_buffer.advance_round()
            self._metrics.update_async(self._async_buffer.buffer_status())

        # Selection fairness
        if self._selector is not None:
            self._metrics.update_selection(self._selector.summary())

        # Step 8: Save model + evaluate
        model = self._save_model(aggregated, server_round)
        eval_metrics: dict = {}
        if model is not None:
            eval_metrics = self._evaluate_on_test_set(model, server_round)

        # Step 9: Flower metrics dict
        flower_metrics: Dict[str, Scalar] = {
            "round": server_round,
            "clients_used": audit["used"],
            "clients_excluded": len(audit["excluded"]),
            "aggregation_method": audit["method"],
            "server_dp_applied": int(audit.get("server_dp_applied", False)),
        }
        flower_metrics.update({k: v for k, v in eval_metrics.items() if isinstance(v, (int, float, str))})

        # Step 10: Commit metrics
        self._metrics.update_bulk({
            "threat_f1": eval_metrics.get("threat_f1"),
            "attack_accuracy": eval_metrics.get("attack_accuracy"),
            "confidence_brier": eval_metrics.get("confidence_brier"),
            "round_loss": float(sum(client_losses) / max(len(client_losses), 1)),
            "clients_used": audit["used"],
            "clients_excluded": len(audit["excluded"]),
            "aggregation_method": audit["method"],
        })
        committed = self._metrics.commit(server_round)

        if self._convergence_tracker is not None and eval_metrics.get("threat_f1"):
            self._convergence_tracker.record(server_round, eval_metrics["threat_f1"])

        logger.info(
            "Round %d complete | used=%d/%d | method=%s | F1=%.4f | ε(server_dp)=%.4f",
            server_round,
            audit["used"], audit["total_clients"],
            audit["method"],
            eval_metrics.get("threat_f1", 0.0),
            server_dp_scale,
        )

        return ndarrays_to_parameters(aggregated), flower_metrics

    def _save_model(
        self,
        weights: List[np.ndarray],
        server_round: int,
    ) -> Optional[object]:
        """Save global model weights to fl_model.pth."""
        model = build_model(_MODEL_CFG)
        set_model_weights(model, weights)
        save_path = _BASE / _PATHS_CFG.get("model_save", "models/fl_model.pth")
        torch.save(model.state_dict(), save_path)
        metadata_path = write_fl_checkpoint_metadata(
            save_path,
            model_config=_MODEL_CFG,
            data_config=_DATA_CFG,
            federation_round=server_round,
        )
        logger.info("Global model saved → %s", save_path)
        logger.info("Global model metadata saved → %s", metadata_path)
        return model

    def _evaluate_on_test_set(self, model, server_round: int) -> Dict[str, float]:
        """
        Run inference on datasets/processed/test.csv.
        Computes threat_f1 and attack_accuracy.
        Also writes to the legacy fl_round_metrics.csv for backward compatibility.
        """
        test_csv = _PROC / "test.csv"
        if not test_csv.exists():
            return {}

        try:
            from sklearn.metrics import f1_score, accuracy_score
            from torch.utils.data import DataLoader, TensorDataset

            import pandas as pd

            windows = build_temporal_windows(
                pd.read_csv(test_csv),
                _MODEL_CFG["sequence_len"],
                stride=int(_DATA_CFG.get("sequence_stride", 1)),
            )
            if not len(windows):
                return {}

            X_t = torch.tensor(windows.features, dtype=torch.float32)
            y_j_list = windows.threat_labels[:, 0].astype(np.int8).tolist()
            y_a_list = windows.attack_labels.tolist()
            y_j_t = torch.tensor(y_j_list, dtype=torch.float32)
            y_a_t = torch.tensor(y_a_list, dtype=torch.long)

            model.eval()
            preds_j, preds_a, confidences = [], [], []
            loader = DataLoader(TensorDataset(X_t, y_j_t, y_a_t), batch_size=256)
            with torch.no_grad():
                for xb, _, _ in loader:
                    out = model(xb)
                    threat_score = out.path_scores.mean(dim=1).numpy()
                    preds_j.extend((threat_score > 0.5).astype(int).tolist())
                    preds_a.extend(out.attack_logits.argmax(dim=1).numpy().tolist())
                    confidences.extend(out.confidence.squeeze(-1).numpy().tolist())

            threat_f1 = float(f1_score(y_j_list, preds_j, average="macro", zero_division=0))
            atk_acc = float(accuracy_score(y_a_list, preds_a))
            correctness = 0.5 * (
                (np.asarray(preds_j) == np.asarray(y_j_list)).astype(np.float32)
                + (np.asarray(preds_a) == np.asarray(y_a_list)).astype(np.float32)
            )
            confidence_brier = float(np.mean(
                (np.asarray(confidences, dtype=np.float32) - correctness) ** 2
            ))

            logger.info(
                "Round %d eval | threat_F1=%.4f | attack_acc=%.4f | confidence_brier=%.4f",
                server_round, threat_f1, atk_acc, confidence_brier,
            )

            # Legacy CSV (backward compatibility)
            csv_path = _RESULTS / "fl_round_metrics.csv"
            mode = "w" if server_round == 1 else "a"
            with open(csv_path, mode, newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "round",
                        "threat_f1",
                        "attack_accuracy",
                        "confidence_brier",
                    ],
                )
                if mode == "w":
                    writer.writeheader()
                writer.writerow({
                    "round": server_round,
                    "threat_f1": round(threat_f1, 4),
                    "attack_accuracy": round(atk_acc, 4),
                    "confidence_brier": round(confidence_brier, 4),
                })

            return {
                "threat_f1": threat_f1,
                "attack_accuracy": atk_acc,
                "confidence_brier": confidence_brier,
            }

        except Exception as exc:
            logger.warning("Test-set evaluation failed (round %d): %s", server_round, exc)
            return {}

    def get_benchmark_summary(self) -> dict:
        """Return benchmark summary for reporting."""
        summary = self._metrics.benchmark_summary()
        if self._trust:
            summary["trust"] = self._trust.summary()
        if self._selector:
            summary["selection"] = self._selector.summary()
        if self._convergence_tracker:
            summary["convergence"] = self._convergence_tracker.summary()
        return summary


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def run_server(num_rounds: int = _FED_CFG["num_rounds"]):
    model = build_model(_MODEL_CFG)
    initial_weights = [val.cpu().numpy() for val in model.state_dict().values()]
    initial_params = ndarrays_to_parameters(initial_weights)

    strategy = SecureFedAvgV2(
        initial_parameters=initial_params,
        min_fit_clients=_FED_CFG["min_clients"],
        min_evaluate_clients=_FED_CFG["min_clients"],
        min_available_clients=_FED_CFG["min_clients"],
        fraction_fit=_FED_CFG["fraction_fit"],
        fraction_evaluate=1.0,
        on_fit_config_fn=lambda server_round: {
            "server_round": server_round,
            "total_rounds": num_rounds,
        },
    )

    logger.info(
        "Starting FLARE v2 FL server on %s for %d rounds.",
        _FED_CFG["server_address"], num_rounds,
    )
    fl.server.start_server(
        server_address=_FED_CFG["server_address"],
        config=fl.server.ServerConfig(num_rounds=num_rounds),
        strategy=strategy,
    )

    # Print benchmark summary after all rounds
    bench = strategy.get_benchmark_summary()
    logger.info("=== FLARE v2 Benchmark Summary ===\n%s", json.dumps(bench, indent=2))


def main():
    parser = argparse.ArgumentParser(description="FL Aggregation Server (FLARE v2)")
    parser.add_argument("--rounds", type=int, default=_FED_CFG["num_rounds"])
    parser.add_argument("--retrain", action="store_true", help="Retraining mode")
    args = parser.parse_args()
    run_server(num_rounds=args.rounds)


if __name__ == "__main__":
    main()
