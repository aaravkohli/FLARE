"""
fl/client.py — Upgraded FL Client  [FLARE v2]

Flower federated learning client running on each simulated drone node.

New in FLARE v2:
  - Client-side DP-SGD (Opacus or manual gradient perturbation)
  - Gradient compression before upload (Top-K + quantization)
  - Personalized model adaptation (pFedMe Moreau envelope)
  - Multi-signal drift reporting to server
  - Privacy budget tracking per client

Backward-compatible:
  - Same Flower NumPyClient interface (fit / evaluate / get_parameters)
  - Same data loading (synthetic + real)
  - Same BiLSTM model architecture

Usage (synthetic mode):
  python fl/client.py --client_id drone_1 --server_address 127.0.0.1:8090

Usage (real data mode):
  python fl/client.py --client_id drone_1 --real --server_address 127.0.0.1:8090

Usage (with DP enabled):
  python fl/client.py --client_id drone_1 --dp

References:
  Abadi et al. (2016) — DP-SGD
  T. Dinh et al. (2020) — pFedMe (personalization)
  Alistarh et al. (2018) — Top-K compression
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from scipy.stats import ks_2samp
import flwr as fl
import yaml
import json
import pandas as pd

from fl.model import BiLSTMAttention, build_model, get_model_weights, set_model_weights

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
_FL_CFG = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_MODEL_CFG = _FL_CFG["model"]
_TRAIN_CFG = _FL_CFG["training"]
_DRIFT_CFG = _FL_CFG["drift_detection"]
_DP_CFG = _FL_CFG.get("differential_privacy", {})
_COMP_CFG = _FL_CFG.get("compression", {})
_PERSONA_CFG = _FL_CFG.get("personalization", {})
_PROC = _BASE / "datasets" / "processed"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLIENT] %(message)s")
logger = logging.getLogger(__name__)

ATTACK_CLASSES = ["none", "barrage", "sweep", "spot", "unknown"]


def _get_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


# ---------------------------------------------------------------------------
# Synthetic data loader (backward compatible)
# ---------------------------------------------------------------------------

def load_local_data(
    client_id: str,
    seq_len: int = 10,
    num_samples: int = 300,
    jammed: bool = False,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Generate synthetic RF sequences for local training.

    Returns:
        X        : [N, seq_len, 5] input sequences
        y_threat : [N, 3] binary threat labels per path
        y_attack : [N]    attack class index
    """
    rng = np.random.default_rng(seed=hash(client_id) % (2**32))

    X_list, y_threat_list, y_attack_list = [], [], []
    for _ in range(num_samples):
        jammed_paths = rng.choice([True, False], size=3, p=[0.3, 0.7])
        if jammed:
            jammed_paths = np.array([True, False, False])

        step = []
        for path_jammed in jammed_paths:
            if path_jammed:
                rssi = rng.uniform(-110.0, -85.0)
                pdr = rng.uniform(0.0, 0.4)
                sinr = rng.uniform(-5.0, 5.0)
                latency = rng.uniform(200.0, 800.0)
                packet_loss = rng.uniform(0.3, 0.9)
            else:
                rssi = rng.uniform(-75.0, -40.0)
                pdr = rng.uniform(0.7, 1.0)
                sinr = rng.uniform(10.0, 30.0)
                latency = rng.uniform(5.0, 80.0)
                packet_loss = rng.uniform(0.0, 0.1)
            step.extend([rssi, pdr, sinr, latency, packet_loss])

        feats_single = np.array(step)[:5].astype(np.float32)
        mins = np.array([-120.0, 0.0, -10.0, 0.0, 0.0], dtype=np.float32)
        maxs = np.array([-20.0, 1.0, 30.0, 1000.0, 1.0], dtype=np.float32)
        feats_norm = (feats_single - mins) / (maxs - mins + 1e-8)
        feats_norm = np.clip(feats_norm, 0.0, 1.0)
        seq_arr = np.tile(feats_norm, (seq_len, 1)).astype(np.float32)

        X_list.append(seq_arr)
        y_threat_list.append(jammed_paths.astype(np.float32))

        if jammed_paths.any():
            attack_idx = rng.integers(1, 5)
        else:
            attack_idx = 0
        y_attack_list.append(attack_idx)

    X = torch.tensor(np.stack(X_list), dtype=torch.float32)
    y_threat = torch.tensor(np.stack(y_threat_list), dtype=torch.float32)
    y_attack = torch.tensor(np.array(y_attack_list), dtype=torch.long)
    return X, y_threat, y_attack


# ---------------------------------------------------------------------------
# Real data loader (RadioML + DroneRF processed CSVs — backward compatible)
# ---------------------------------------------------------------------------

def load_real_data(
    client_id: str,
    seq_len: int = 10,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Load real RF data for a single drone client from the preprocessed CSV.
    Falls back to synthetic data if the CSV is not found.
    """
    drone_num = "".join(filter(str.isdigit, client_id)) or "1"
    csv_path = _PROC / f"drone_{drone_num}_train.csv"

    if not csv_path.exists():
        logger.warning(
            "[%s] Real data CSV not found: %s — falling back to synthetic.",
            client_id, csv_path,
        )
        return load_local_data(client_id=client_id, seq_len=seq_len, num_samples=500)

    stats_path = _PROC / "dataset_stats.json"
    if stats_path.exists():
        _stats = json.loads(stats_path.read_text())
        logger.debug("[%s] Using dataset stats from %s", client_id, stats_path)

    df = pd.read_csv(csv_path)
    feature_cols = ["rssi", "pdr", "sinr", "latency", "packet_loss"]
    attack_col = "attack_class" if "attack_class" in df.columns else "jammed"
    df = df.dropna(subset=feature_cols + ["jammed"]).reset_index(drop=True)

    if len(df) < seq_len + 1:
        logger.warning("[%s] Too few rows in %s — falling back to synthetic.", client_id, csv_path)
        return load_local_data(client_id=client_id, seq_len=seq_len, num_samples=500)

    feats = df[feature_cols].values.astype(np.float32)
    _MINS = np.array([-120.0, 0.0, -10.0, 0.0, 0.0], dtype=np.float32)
    _MAXS = np.array([-20.0, 1.0, 30.0, 1000.0, 1.0], dtype=np.float32)
    for i, col in enumerate(feature_cols):
        col_min, col_max = feats[:, i].min(), feats[:, i].max()
        if col_max > 1.01 or col_min < -0.01:
            logger.info("[%s] Normalising column '%s' (range %.2f–%.2f)", client_id, col, col_min, col_max)
            feats[:, i] = (feats[:, i] - _MINS[i]) / (_MAXS[i] - _MINS[i] + 1e-8)
            feats[:, i] = np.clip(feats[:, i], 0.0, 1.0)

    jammed_arr = df["jammed"].values.astype(np.int8)
    if attack_col in df.columns:
        attack_arr = df[attack_col].values.astype(np.int64)
    else:
        attack_arr = jammed_arr.astype(np.int64)
    attack_arr = np.clip(attack_arr, 0, len(ATTACK_CLASSES) - 1)

    X_list, y_threat_list, y_attack_list = [], [], []
    for i in range(len(feats)):
        window = np.tile(feats[i], (seq_len, 1))
        label = jammed_arr[i]
        atk = attack_arr[i]
        X_list.append(window)
        y_threat_list.append(np.array([label, label, label], dtype=np.float32))
        y_attack_list.append(atk)

    X = torch.tensor(np.stack(X_list), dtype=torch.float32)
    y_threat = torch.tensor(np.stack(y_threat_list), dtype=torch.float32)
    y_attack = torch.tensor(np.array(y_attack_list), dtype=torch.long)

    logger.info(
        "[%s] Loaded %d sequences from %s (jammed=%.1f%%)",
        client_id, len(X), csv_path.name, 100 * jammed_arr.mean(),
    )
    return X, y_threat, y_attack


# ---------------------------------------------------------------------------
# Concept Drift Detection (backward compatible, unified with fl.drift)
# ---------------------------------------------------------------------------

_rssi_baseline: Optional[np.ndarray] = None


def check_concept_drift(current_rssi: np.ndarray) -> bool:
    """
    KS test between current RSSI distribution and baseline.
    Returns True if drift is detected (p < threshold).
    """
    global _rssi_baseline
    if not _DRIFT_CFG.get("enabled", True):
        return False

    if _rssi_baseline is None:
        _rssi_baseline = current_rssi.copy()
        return False

    p_threshold = _DRIFT_CFG.get("ks_p_threshold", 0.05)
    _, p_value = ks_2samp(_rssi_baseline, current_rssi)
    drift = p_value < p_threshold
    if drift:
        logger.warning(
            "Concept drift detected (KS p=%.4f < %.4f). Resetting RSSI baseline.",
            p_value, p_threshold,
        )
        _rssi_baseline = current_rssi.copy()
    return drift


# ---------------------------------------------------------------------------
# Upgraded Flower Client
# ---------------------------------------------------------------------------

class DroneFlClient(fl.client.NumPyClient):
    """
    Upgraded Flower NumPyClient for a single drone node.

    New in FLARE v2:
      - Optional DP-SGD training (client-side Gaussian mechanism)
      - Optional gradient compression before upload
      - Optional personalized model adaptation (pFedMe)
      - Multi-signal drift score reporting
      - Privacy budget tracking

    All new features are gated by fl_config.yaml feature flags and
    fall back gracefully to v1 behavior when disabled.
    """

    def __init__(
        self,
        client_id: str,
        jammed: bool = False,
        use_real_data: bool = False,
        enable_dp: Optional[bool] = None,
        enable_compression: Optional[bool] = None,
        enable_personalization: Optional[bool] = None,
    ):
        self.client_id = client_id
        self.device = _get_device()
        logger.info("Client %s using device: %s", client_id, self.device)

        # Feature flags (config → override)
        dp_client_cfg = _DP_CFG.get("client_side", {})
        self._dp_enabled = (
            enable_dp
            if enable_dp is not None
            else (_DP_CFG.get("enabled", False) and dp_client_cfg.get("enabled", True))
        )
        self._comp_enabled = (
            enable_compression
            if enable_compression is not None
            else _COMP_CFG.get("enabled", False)
        )
        self._persona_enabled = (
            enable_personalization
            if enable_personalization is not None
            else _PERSONA_CFG.get("enabled", False)
        )

        # Build model
        self.model = build_model(_MODEL_CFG).to(self.device)
        self.jammed = jammed
        self.use_real_data = use_real_data

        # Load training data once
        if use_real_data:
            self.X, self.y_threat, self.y_attack = load_real_data(
                client_id=client_id, seq_len=_MODEL_CFG["sequence_len"]
            )
        else:
            self.X, self.y_threat, self.y_attack = load_local_data(
                client_id=client_id,
                seq_len=_MODEL_CFG["sequence_len"],
                num_samples=500,
                jammed=jammed,
            )

        logger.info(
            "Client %s: %d samples | mode=%s | jammed=%s | DP=%s | compress=%s | persona=%s",
            client_id, len(self.X),
            "real" if use_real_data else "synthetic",
            jammed, self._dp_enabled, self._comp_enabled, self._persona_enabled,
        )

        # Concept drift: track RSSI across rounds
        self._rssi_values = self.X[:, :, 0].numpy().flatten()

        # Gradient compression (per-client error buffer)
        if self._comp_enabled:
            from fl.compression import GradientCompressor
            comp_strategy = _COMP_CFG.get("strategy", "topk")
            topk_cfg = _COMP_CFG.get("topk", {})
            quant_cfg = _COMP_CFG.get("quantize", {})
            self._compressor = GradientCompressor(
                strategy=comp_strategy,
                topk_ratio=topk_cfg.get("ratio", 0.10),
                quantize_bits=quant_cfg.get("bits", 8),
                error_feedback=topk_cfg.get("error_feedback", True),
                client_id=client_id,
            )
        else:
            self._compressor = None

        # Personalization manager
        if self._persona_enabled:
            from fl.personalization import PersonalizationManager
            persona_save_dir = str(_BASE / _FL_CFG.get("paths", {}).get(
                "personalized_models", "models/personalized"
            ))
            self._persona_manager = PersonalizationManager(
                client_id=client_id,
                model_builder=build_model,
                model_cfg=_MODEL_CFG,
                lambda_prox=_PERSONA_CFG.get("lambda_prox", 0.1),
                local_adapt_steps=_PERSONA_CFG.get("local_adapt_steps", 3),
                head_only=_PERSONA_CFG.get("head_only", True),
                save_dir=persona_save_dir,
                device=self.device,
                enabled=True,
            )
        else:
            self._persona_manager = None

        # Privacy accountant
        if self._dp_enabled:
            from fl.privacy import PrivacyAccountant
            self._privacy_accountant = PrivacyAccountant(
                noise_multiplier=dp_client_cfg.get("noise_multiplier", 1.1),
                sample_rate=_TRAIN_CFG["batch_size"] / max(len(self.X), 1),
                delta=dp_client_cfg.get("delta", 1e-5),
                alphas=dp_client_cfg.get("rdp_alpha_orders", None),
            )
        else:
            self._privacy_accountant = None

        # Drift state (for cosine similarity tracking)
        self._prev_grad_flat: Optional[np.ndarray] = None
        self._prev_loss: Optional[float] = None

    # -----------------------------------------------------------------------
    # Flower API
    # -----------------------------------------------------------------------

    def get_parameters(self, config: dict) -> List[np.ndarray]:
        return get_model_weights(self.model)

    def fit(
        self, parameters: List[np.ndarray], config: dict
    ) -> Tuple[List[np.ndarray], int, dict]:
        """
        Local training pipeline (FLARE v2):
          1. Set global model weights
          2. Check concept drift
          3. (If DP enabled) wrap with DP-SGD
          4. Train for local_epochs
          5. (If personalization enabled) run pFedMe adaptation
          6. (If compression enabled) compress weight deltas
          7. Report multi-signal drift + privacy metrics
        """
        set_model_weights(self.model, parameters)

        # Step 2: Concept drift check
        drift = check_concept_drift(self._rssi_values)

        # Step 3: DP-SGD setup
        dp_client_cfg = _DP_CFG.get("client_side", {})
        dp_epsilon, dp_delta = None, None
        use_opacus = self._dp_enabled

        optimizer = torch.optim.Adam(self.model.parameters(), lr=_TRAIN_CFG["learning_rate"])
        bce_loss = nn.BCELoss()
        ce_loss = nn.CrossEntropyLoss()
        w_threat = _TRAIN_CFG["loss_weight_threat"]
        w_attack = _TRAIN_CFG["loss_weight_attack"]

        dataset = TensorDataset(self.X, self.y_threat, self.y_attack)
        loader = DataLoader(dataset, batch_size=_TRAIN_CFG["batch_size"], shuffle=True)

        # Attempt Opacus wrapping (with fallback)
        privacy_engine = None
        if use_opacus:
            try:
                from fl.privacy import make_opacus_private_engine
                private_model, private_optimizer, privacy_engine, noise_mult = make_opacus_private_engine(
                    model=self.model,
                    optimizer=optimizer,
                    data_loader=loader,
                    target_epsilon=dp_client_cfg.get("epsilon", 5.0),
                    target_delta=dp_client_cfg.get("delta", 1e-5),
                    max_grad_norm=dp_client_cfg.get("max_grad_norm", 1.0),
                    epochs=_TRAIN_CFG["local_epochs"],
                )
                if privacy_engine is not None:
                    self.model = private_model
                    optimizer = private_optimizer
                    logger.info("[%s] Opacus DP-SGD activated", self.client_id)
                else:
                    use_opacus = False  # Fallback to manual
            except Exception as exc:
                logger.warning("[%s] Opacus initialization failed: %s. Using manual DP.", self.client_id, exc)
                use_opacus = False

        # Step 4: Local training loop
        self.model.train()
        epoch_loss = 0.0
        n_batches = 0
        total_grad_norm = 0.0

        for epoch in range(_TRAIN_CFG["local_epochs"]):
            for xb, yb_threat, yb_attack in loader:
                xb = xb.to(self.device)
                yb_threat = yb_threat.to(self.device)
                yb_attack = yb_attack.to(self.device)

                out = self.model(xb)
                loss_threat = bce_loss(out.path_scores, yb_threat)
                loss_attack = ce_loss(out.attack_logits, yb_attack)
                loss = w_threat * loss_threat + w_attack * loss_attack

                optimizer.zero_grad()
                loss.backward()

                # Manual DP-SGD (fallback if Opacus unavailable)
                if self._dp_enabled and not use_opacus and privacy_engine is None:
                    from fl.privacy import apply_client_dp_step
                    grad_norm = apply_client_dp_step(
                        self.model,
                        max_grad_norm=dp_client_cfg.get("max_grad_norm", 1.0),
                        noise_multiplier=dp_client_cfg.get("noise_multiplier", 1.1),
                    )
                    total_grad_norm += grad_norm

                optimizer.step()
                epoch_loss += loss.item()
                n_batches += 1

            logger.info(
                "  [%s] Epoch %d/%d loss=%.4f",
                self.client_id, epoch + 1, _TRAIN_CFG["local_epochs"],
                epoch_loss / max(n_batches, 1),
            )

        avg_loss = epoch_loss / max(n_batches, 1)

        # Privacy accounting
        if self._privacy_accountant is not None:
            self._privacy_accountant.step(n_batches)
            dp_eps, dp_delta = self._privacy_accountant.compute_epsilon()
            logger.info("[%s] DP budget used: ε=%.4f, δ=%.2e", self.client_id, dp_eps, dp_delta)
        elif privacy_engine is not None:
            try:
                dp_eps = privacy_engine.get_epsilon(delta=dp_client_cfg.get("delta", 1e-5))
                dp_delta = dp_client_cfg.get("delta", 1e-5)
            except Exception:
                dp_eps, dp_delta = None, None

        # Step 5: Personalization (pFedMe adaptation)
        persona_metrics = {}
        if self._persona_enabled and self._persona_manager is not None:
            personal_model = self._persona_manager.load_or_init(parameters)
            persona_metrics = self._persona_manager.adapt(
                self.X, self.y_threat, self.y_attack,
                global_weights=parameters,
                batch_size=_TRAIN_CFG["batch_size"],
                lr=_TRAIN_CFG["learning_rate"],
            )
            self._persona_manager.save()

        # Get model weights to send to server
        updated_weights = get_model_weights(self.model)

        # Step 6: Gradient compression
        compression_stats = {}
        weights_to_send = updated_weights
        if self._comp_enabled and self._compressor is not None:
            # Compute delta from global model for compression
            global_w = parameters
            deltas = [uw - gw for uw, gw in zip(updated_weights, global_w)]
            payload, meta = self._compressor.compress(deltas)
            compressed_deltas = self._compressor.decompress(payload, meta)
            self._compressor.update_error_feedback(deltas, compressed_deltas)
            # Reconstruct weights from compressed deltas
            weights_to_send = [gw + cd for gw, cd in zip(global_w, compressed_deltas)]
            compression_stats = self._compressor.get_round_stats()

        # Step 7: Drift score for cosine signal
        curr_flat = np.concatenate([w.flatten() for w in updated_weights])
        cos_sim = 1.0
        if self._prev_grad_flat is not None and self._prev_grad_flat.shape == curr_flat.shape:
            n1 = np.linalg.norm(self._prev_grad_flat)
            n2 = np.linalg.norm(curr_flat)
            if n1 > 1e-10 and n2 > 1e-10:
                cos_sim = float(np.dot(self._prev_grad_flat, curr_flat) / (n1 * n2))
        self._prev_grad_flat = curr_flat
        self._prev_loss = avg_loss

        # Build metrics dict
        metrics: Dict[str, object] = {
            "train_loss": float(avg_loss),
            "concept_drift": int(drift),
            "client_id": self.client_id,
            "cosine_similarity": float(cos_sim),
            "rssi_values_json": json.dumps(self._rssi_values[:50].tolist()),  # sample for KS
            # DP metrics
            "dp_epsilon": float(dp_eps) if dp_eps is not None else 0.0,
            "dp_delta": float(dp_delta) if dp_delta is not None else 0.0,
            # Compression metrics
            "compression_ratio": compression_stats.get("compression_ratio", 1.0),
            "bytes_saved": compression_stats.get("original_bytes", 0) - compression_stats.get("compressed_bytes", 0),
            # Personalization
            "persona_delta": persona_metrics.get("persona_delta", 0.0),
            "personal_loss": persona_metrics.get("personal_loss", 0.0),
        }

        logger.info(
            "  [%s] Fit complete: loss=%.4f | ε=%.4f | compress=%.1f× | cos_sim=%.4f | drift=%s",
            self.client_id, avg_loss,
            metrics["dp_epsilon"], metrics["compression_ratio"],
            metrics["cosine_similarity"], drift,
        )

        return weights_to_send, len(self.X), metrics

    def evaluate(
        self, parameters: List[np.ndarray], config: dict
    ) -> Tuple[float, int, dict]:
        """Evaluate global model on local data."""
        set_model_weights(self.model, parameters)
        self.model.eval()

        bce_loss = nn.BCELoss()
        dataset = TensorDataset(self.X, self.y_threat, self.y_attack)
        loader = DataLoader(dataset, batch_size=_TRAIN_CFG["batch_size"])

        total_loss = 0.0
        correct_threat = 0
        total = 0

        with torch.no_grad():
            for xb, yb_threat, yb_attack in loader:
                xb = xb.to(self.device)
                yb_threat = yb_threat.to(self.device)
                out = self.model(xb)
                total_loss += bce_loss(out.path_scores, yb_threat).item()
                preds = (out.path_scores > 0.5).float()
                correct_threat += (preds == yb_threat).all(dim=1).sum().item()
                total += len(xb)

        avg_loss = total_loss / max(len(loader), 1)
        accuracy = correct_threat / max(total, 1)

        logger.info("  [%s] Eval loss=%.4f acc=%.4f", self.client_id, avg_loss, accuracy)
        return avg_loss, total, {"accuracy": accuracy}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Drone FL Client (FLARE v2)")
    parser.add_argument("--client_id", type=str, default="drone_1")
    parser.add_argument("--server_address", type=str, default="127.0.0.1:8090")
    parser.add_argument("--jammed", action="store_true")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--dp", action="store_true", help="Enable client-side DP-SGD")
    parser.add_argument("--compress", action="store_true", help="Enable gradient compression")
    parser.add_argument("--persona", action="store_true", help="Enable personalized FL")
    args = parser.parse_args()

    logger.info(
        "Starting FL client v2: %s → %s (real=%s, jammed=%s, dp=%s, compress=%s, persona=%s)",
        args.client_id, args.server_address, args.real, args.jammed,
        args.dp, args.compress, args.persona,
    )

    client = DroneFlClient(
        client_id=args.client_id,
        jammed=args.jammed,
        use_real_data=args.real,
        enable_dp=args.dp or None,
        enable_compression=args.compress or None,
        enable_personalization=args.persona or None,
    )
    fl.client.start_numpy_client(server_address=args.server_address, client=client)


if __name__ == "__main__":
    main()
