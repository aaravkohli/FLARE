"""
fl/server.py — Upgraded FL Server  [FLARE v2]

Flower FL server integrating all FLARE v2 components:
  1. Optional client-selection metrics (PoCo + UCB utility is opt-in)
  2. Persisted Trust + Reputation Engine (EMA scoring, quarantine)
  3. Client Drift Detection (cosine, KS, loss spike)
  4. Byzantine-resilient aggregation (robust analysis, count caps, adaptive
     clipping, trust weighting, robust fallback, optional DP)
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

from fl.aggregator import TrustWeightedAggregationStrategy, secure_aggregate
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
_BYZ_CFG = _FL_CFG.get("byzantine_aggregation", {})
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

    def __init__(
        self,
        initial_parameters: Parameters,
        *,
        checkpoint_output_path: Path | None = None,
        **kwargs,
    ):
        super().__init__(initial_parameters=initial_parameters, **kwargs)
        configured_output = checkpoint_output_path or Path(
            _PATHS_CFG.get("training_candidate_save", "models/fl_candidate.pth")
        )
        self._checkpoint_output_path = (
            configured_output
            if configured_output.is_absolute()
            else _BASE / configured_output
        )
        self._global_weights: Optional[List[np.ndarray]] = parameters_to_ndarrays(
            initial_parameters
        )
        from fl.trust import ClientIdentityRegistry
        from fleet.registry import active_drone_ids
        self._identity_registry = ClientIdentityRegistry(
            allowed_client_ids=_FED_CFG.get("allowed_client_ids"),
            allowed_client_resolver=active_drone_ids,
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
            "async=%s | kd=%s | persona=%s | byzantine=%s",
            self._trust is not None,
            _DRIFT_CFG_V2.get("enabled", True),
            _SEL_CFG.get("enabled", False),
            _ASYNC_CFG.get("enabled", False),
            _KD_CFG.get("enabled", False),
            _PERSONA_CFG.get("enabled", False),
            self._byzantine_strategy is not None,
        )

    def _init_trust(self):
        if _TRUST_CFG.get("enabled", False) and _BYZ_CFG.get("enabled", True):
            from fl.trust import TrustManager, UpdateAnalyzer
            self._trust = TrustManager(
                history_alpha=_TRUST_CFG.get("history_alpha", _TRUST_CFG.get("alpha", 0.7)),
                initial_trust=_TRUST_CFG.get("initial_trust", 0.8),
                suspicious_weight=_TRUST_CFG.get("suspicious_weight", 0.25),
                reject_trust_threshold=_TRUST_CFG.get("reject_trust_threshold", 0.15),
                suspicious_trust_threshold=_TRUST_CFG.get("suspicious_trust_threshold", 0.5),
                quarantine_rounds=_TRUST_CFG.get("quarantine_rounds", 3),
                quarantine_trigger_rounds=_TRUST_CFG.get("quarantine_trigger_rounds", 2),
                history_size=_TRUST_CFG.get("history_size", 20),
                state_path=os.getenv(
                    "FLARE_FL_TRUST_STATE",
                    str(_BASE / _PATHS_CFG.get("trust_state", "results/fl_trust_state.json")),
                ),
            )
            analyzer = UpdateAnalyzer(
                robust_z_threshold=_BYZ_CFG.get("robust_z_threshold", 3.5),
                suspicious_score=_BYZ_CFG.get("suspicious_score", 0.35),
                malicious_score=_BYZ_CFG.get("malicious_score", 0.75),
                min_clients=_BYZ_CFG.get("min_clients_for_detection", 3),
                signal_weights=_BYZ_CFG.get("signal_weights"),
                heterogeneity_tolerance=_BYZ_CFG.get("heterogeneity_tolerance", 0.75),
                heterogeneous_population_threshold=_BYZ_CFG.get(
                    "heterogeneous_population_threshold", 0.35
                ),
                suspicious_min_evidence=_BYZ_CFG.get("suspicious_min_evidence", 2),
                extreme_z_threshold=_BYZ_CFG.get("extreme_z_threshold", 8.0),
                extreme_norm_ratio=_BYZ_CFG.get("extreme_norm_ratio", 5.0),
                opposing_cosine_threshold=_BYZ_CFG.get(
                    "opposing_cosine_threshold", -0.10
                ),
            )
            server_dp_cfg = _DP_CFG.get("server_side", {})
            server_dp_scale = (
                server_dp_cfg.get("noise_scale", 0.01)
                if _DP_CFG.get("enabled", False) and server_dp_cfg.get("enabled", True)
                else 0.0
            )
            self._byzantine_strategy = TrustWeightedAggregationStrategy(
                analyzer=analyzer,
                trust_manager=self._trust,
                clip_norm=_SEC_CFG["clip_norm"],
                suspicious_fraction_for_fallback=_BYZ_CFG.get(
                    "suspicious_fraction_for_fallback", 0.5
                ),
                fallback_strategy=_BYZ_CFG.get("fallback_strategy", "coordinate_median"),
                trim_ratio=_SEC_CFG["trim_ratio"],
                server_dp_noise_scale=server_dp_scale,
                server_dp_sensitivity=_SEC_CFG["clip_norm"],
                adaptive_clip_enabled=_BYZ_CFG.get("adaptive_clip_enabled", True),
                clip_mad_multiplier=_BYZ_CFG.get("clip_mad_multiplier", 3.0),
                min_clip_norm=_BYZ_CFG.get("min_clip_norm", 1e-6),
                max_sample_count_ratio=_BYZ_CFG.get("max_sample_count_ratio", 3.0),
                sample_count_iqr_multiplier=_BYZ_CFG.get(
                    "sample_count_iqr_multiplier", 1.5
                ),
            )
        else:
            self._trust = None
            self._byzantine_strategy = None

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
                min_teacher_disagreement=_KD_CFG.get(
                    "min_teacher_disagreement", 0.0
                ),
                max_proxy_kl_regression=_KD_CFG.get(
                    "max_proxy_kl_regression", 0.0
                ),
                max_validation_loss_regression=_KD_CFG.get(
                    "max_validation_loss_regression", 0.0
                ),
            )
        else:
            self._distillation = None

    def _init_metrics(self):
        from fl.metrics import FLMetricsTracker
        metrics_csv = os.getenv(
            "FLARE_FL_METRICS_CSV",
            str(_BASE / _PATHS_CFG.get("metrics_csv", "results/fl_advanced_metrics.csv")),
        )
        metrics_json = os.getenv(
            "FLARE_FL_METRICS_JSON",
            str(_BASE / _PATHS_CFG.get("metrics_json", "results/fl_metrics_snapshot.json")),
        )
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
          1. Extract updates and bind each connection to a drone identity
          2. Record client-selection and drift signals
          3. Analyze update deltas with robust population statistics
          4. Update persistent trust and reject/down-weight/quarantine clients
          5. Cap sample claims, adaptively clip, and robustly aggregate
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
        forced_rejections: Dict[int, str] = {}
        self._identity_registry.begin_round()

        for client_proxy, fit_res in results:
            w = parameters_to_ndarrays(fit_res.parameters)
            client_weights.append(w)
            num_examples.append(fit_res.num_examples)
            m = fit_res.metrics or {}
            claimed_client_id = str(m.get("client_id", ""))
            resolved_client_id, identity_error = self._identity_registry.resolve(
                str(client_proxy.cid), claimed_client_id
            )
            client_ids.append(resolved_client_id)
            if identity_error:
                forced_rejections[len(client_ids) - 1] = identity_error
                logger.warning("[Identity] Rejecting update from %s: %s", client_proxy.cid, identity_error)
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

        # Step 2: client-selection bookkeeping. Trust is calculated later from
        # server-observed model deltas, never from self-reported accuracy.
        if self._selector is not None:
            for cid, metrics in zip(client_ids, client_metrics_list):
                self._selector.update_client_stats(cid, loss=metrics.get("train_loss"))

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

        # Step 4/5: Analyze raw deltas, update longitudinal trust, then reject,
        # down-weight, or robustly aggregate the surviving updates.
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

        if self._byzantine_strategy is not None:
            aggregated, audit = self._byzantine_strategy.aggregate(
                client_ids=client_ids,
                client_weights=client_weights,
                global_weights=baseline,
                num_examples=num_examples,
                forced_rejections=forced_rejections,
            )
            trust_snapshot = self._trust.snapshot()
            trust_summary = self._trust.summary()
            self._metrics.update_trust(trust_summary)
            self._metrics.update_min_trust(trust_snapshot)
            self._metrics.update_security_audit(audit)
        else:
            aggregated, audit = secure_aggregate(
                client_weights=client_weights,
                global_weights=baseline,
                num_examples=num_examples,
                clip_norm=_SEC_CFG["clip_norm"],
                z_threshold=_SEC_CFG["anomaly_z_threshold"],
                use_trimmed_mean=_SEC_CFG["use_trimmed_mean"],
                trim_ratio=_SEC_CFG["trim_ratio"],
                server_dp_noise_scale=server_dp_scale,
                server_dp_sensitivity=_SEC_CFG["clip_norm"],
            )

        self._global_weights = aggregated
        client_security_audits = audit.get("clients")
        trusted_teacher_pairs = [
            (weights, client_audit)
            for weights, client_audit in zip(client_weights, client_security_audits or [])
            if client_audit.get("action") == "ACCEPTED"
        ]
        trusted_teacher_weights = [weights for weights, _ in trusted_teacher_pairs]
        if not trusted_teacher_weights:
            trusted_teacher_pairs = [
                (weights, client_audit)
                for weights, client_audit in zip(client_weights, client_security_audits or [])
                if client_audit.get("action") != "REJECTED"
            ]
            trusted_teacher_weights = [weights for weights, _ in trusted_teacher_pairs]
        if not client_security_audits:
            trusted_teacher_weights = client_weights
            trusted_teacher_pairs = [(weights, {}) for weights in client_weights]

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
            # Rejected clients must not bypass the aggregation defense through
            # the knowledge-distillation teacher ensemble.
            for w in trusted_teacher_weights[:6]:  # cap at 6 teachers for memory
                m = build_model(_MODEL_CFG)
                set_model_weights(m, w)
                client_models.append(m)

            kd_metrics = self._distillation.maybe_distill(
                round_num=server_round,
                global_model=global_model,
                client_models=client_models,
                teacher_weights=(
                    [
                        max(0.0, float(client_audit.get("trust_score", 1.0)))
                        for _, client_audit in trusted_teacher_pairs[:6]
                    ]
                    if _KD_CFG.get("trust_weighted_teachers", True)
                    else None
                ),
                validation_loss_fn=self._distillation_validation_loss,
            )
            if kd_metrics is not None and kd_metrics.get("applied", True):
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
            "suspected_malicious_clients": audit.get("suspected_malicious_clients", 0),
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
            "suspected_malicious_clients": audit.get("suspected_malicious_clients", 0),
            "rejected_updates": audit.get("rejected_updates", len(audit["excluded"])),
        })
        self._metrics.commit(server_round)

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

    def _distillation_validation_loss(self, model) -> float:
        """Deterministic held-out loss used only as a FedDF promotion gate."""
        test_csv = _PROC / "test.csv"
        if not test_csv.exists():
            return 0.0
        import pandas as pd
        import torch.nn.functional as F

        windows = build_temporal_windows(
            pd.read_csv(test_csv),
            _MODEL_CFG["sequence_len"],
            stride=int(_DATA_CFG.get("sequence_stride", 1)),
        )
        if not len(windows):
            return 0.0
        features = torch.tensor(windows.features[:512], dtype=torch.float32)
        threat = torch.tensor(windows.threat_labels[:512], dtype=torch.float32)
        attack = torch.tensor(windows.attack_labels[:512], dtype=torch.long)
        model.eval()
        with torch.no_grad():
            output = model(features)
            return float(
                F.binary_cross_entropy(output.path_scores, threat)
                + F.cross_entropy(output.attack_logits, attack)
            )

    def _save_model(
        self,
        weights: List[np.ndarray],
        server_round: int,
    ) -> Optional[object]:
        """Save the current global weights as a non-deployed candidate."""
        model = build_model(_MODEL_CFG)
        set_model_weights(model, weights)
        save_path = self._checkpoint_output_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
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
            import pandas as pd
            from sklearn.metrics import accuracy_score, f1_score
            from torch.utils.data import DataLoader, TensorDataset

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
            csv_path = Path(os.getenv(
                "FLARE_FL_LEGACY_METRICS_CSV", str(_RESULTS / "fl_round_metrics.csv")
            ))
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

def run_server(
    num_rounds: int = _FED_CFG["num_rounds"],
    *,
    output_path: Path | None = None,
    server_address: str | None = None,
):
    model = build_model(_MODEL_CFG)
    initial_weights = [val.cpu().numpy() for val in model.state_dict().values()]
    initial_params = ndarrays_to_parameters(initial_weights)

    strategy = SecureFedAvgV2(
        initial_parameters=initial_params,
        checkpoint_output_path=output_path,
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

    env_addr = os.getenv("FL_SERVER_ADDRESS")
    if not env_addr and (os.getenv("FL_SERVER_HOST") or os.getenv("FL_SERVER_PORT")):
        host = os.getenv("FL_SERVER_HOST", "0.0.0.0")
        port = os.getenv("FL_SERVER_PORT", "8090")
        env_addr = f"{host}:{port}"
    resolved_server_address = server_address or env_addr or _FED_CFG["server_address"]
    logger.info(
        "Starting FLARE v2 FL server on %s for %d rounds.",
        resolved_server_address, num_rounds,
    )
    fl.server.start_server(
        server_address=resolved_server_address,
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
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Non-deployed candidate checkpoint path",
    )
    parser.add_argument(
        "--server-address",
        default=None,
        help="Listening address override used by isolated integration gates",
    )
    args = parser.parse_args()
    run_server(
        num_rounds=args.rounds,
        output_path=args.output,
        server_address=args.server_address,
    )


if __name__ == "__main__":
    main()
