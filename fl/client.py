"""
fl/client.py — Upgraded FL Client  [FLARE v2]

Flower federated learning client running on each simulated drone node.

New in FLARE v2:
  - Client-side DP-SGD through a persistent Opacus privacy engine
  - Local compression-error simulation (Top-K + quantization)
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
import hashlib
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

from fl.model import (
    BiLSTMAttention,
    build_model,
    confidence_correctness_target,
    get_model_weights,
    set_model_weights,
)
from fl.data import (
    FEATURE_COLS,
    build_temporal_windows,
    correlated_temporal_sequence,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
_FL_CFG = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_MODEL_CFG = _FL_CFG["model"]
_TRAIN_CFG = _FL_CFG["training"]
_FED_CFG = _FL_CFG["federation"]
_DRIFT_CFG = _FL_CFG["drift_detection"]
_DP_CFG = _FL_CFG.get("differential_privacy", {})
_COMP_CFG = _FL_CFG.get("compression", {})
_PERSONA_CFG = _FL_CFG.get("personalization", {})
_DATA_CFG = _FL_CFG.get("data", {})
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
    seed = int.from_bytes(
        hashlib.sha256(client_id.encode("utf-8")).digest()[:8],
        byteorder="big",
    )
    rng = np.random.default_rng(seed=seed)

    X_list, y_threat_list, y_attack_list = [], [], []
    attack_ranges = {
        1: ((-112.0, -96.0), (0.00, 0.20), (-7.0, 0.0), (450.0, 900.0), (0.65, 0.98)),
        2: ((-104.0, -88.0), (0.15, 0.45), (-2.0, 7.0), (220.0, 650.0), (0.35, 0.80)),
        3: ((-98.0, -82.0), (0.25, 0.55), (1.0, 10.0), (130.0, 450.0), (0.25, 0.65)),
        4: ((-110.0, -80.0), (0.05, 0.60), (-5.0, 12.0), (100.0, 850.0), (0.20, 0.90)),
    }

    for _ in range(num_samples):
        path_jammed = bool(jammed or rng.random() < 0.3)
        attack_idx = int(rng.integers(1, 5)) if path_jammed else 0
        if path_jammed:
            ranges = attack_ranges[attack_idx]
            rssi, pdr, sinr, latency, packet_loss = [
                rng.uniform(low, high) for low, high in ranges
            ]
        else:
            rssi = rng.uniform(-75.0, -40.0)
            pdr = rng.uniform(0.7, 1.0)
            sinr = rng.uniform(10.0, 30.0)
            latency = rng.uniform(5.0, 80.0)
            packet_loss = rng.uniform(0.0, 0.1)

        feats_single = np.array(
            [rssi, pdr, sinr, latency, packet_loss], dtype=np.float32
        )
        mins = np.array([-120.0, 0.0, -10.0, 0.0, 0.0], dtype=np.float32)
        maxs = np.array([-20.0, 1.0, 30.0, 1000.0, 1.0], dtype=np.float32)
        feats_norm = (feats_single - mins) / (maxs - mins + 1e-8)
        feats_norm = np.clip(feats_norm, 0.0, 1.0)
        seq_arr = correlated_temporal_sequence(feats_norm, seq_len, rng)

        X_list.append(seq_arr)
        # Runtime evaluates one path at a time and averages the three threat
        # outputs, so all three heads receive the observed path's label.
        y_threat_list.append(np.full(3, float(path_jammed), dtype=np.float32))
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
    *,
    allow_synthetic_fallback: bool = True,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Load real RF data for a single drone client from the preprocessed CSV.
    Falls back to synthetic data only when ``allow_synthetic_fallback`` is true.
    """
    drone_num = "".join(filter(str.isdigit, client_id)) or "1"
    csv_path = _PROC / f"drone_{drone_num}_train.csv"

    if not csv_path.exists():
        if not allow_synthetic_fallback:
            raise FileNotFoundError(
                f"Required real dataset for {client_id} was not found: {csv_path}"
            )
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
    attack_col = "attack_class" if "attack_class" in df.columns else "jammed"
    df = df.dropna(subset=[*FEATURE_COLS, "jammed"]).reset_index(drop=True)

    if len(df) < seq_len:
        if not allow_synthetic_fallback:
            raise ValueError(
                f"Required real dataset {csv_path} has {len(df)} rows; "
                f"at least {seq_len} are required"
            )
        logger.warning("[%s] Too few rows in %s — falling back to synthetic.", client_id, csv_path)
        return load_local_data(client_id=client_id, seq_len=seq_len, num_samples=500)

    if attack_col != "attack_class":
        df["attack_class"] = df[attack_col].astype(np.int64)
    windows = build_temporal_windows(
        df,
        seq_len,
        stride=int(_DATA_CFG.get("sequence_stride", 1)),
    )
    if not len(windows):
        if not allow_synthetic_fallback:
            raise ValueError(
                f"Required real dataset {csv_path} has no complete temporal groups"
            )
        logger.warning(
            "[%s] No complete temporal groups in %s — falling back to synthetic.",
            client_id,
            csv_path,
        )
        return load_local_data(client_id=client_id, seq_len=seq_len, num_samples=500)

    X = torch.tensor(windows.features, dtype=torch.float32)
    y_threat = torch.tensor(windows.threat_labels, dtype=torch.float32)
    y_attack = torch.tensor(windows.attack_labels, dtype=torch.long)

    logger.info(
        "[%s] Loaded %d sequences from %s (jammed=%.1f%%)",
        client_id, len(X), csv_path.name, 100 * float(windows.threat_labels[:, 0].mean()),
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
      - Optional Opacus DP-SGD training (client-side Gaussian mechanism)
      - Optional compression-error simulation before dense Flower upload
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
        require_real_data: bool = False,
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
        if require_real_data and not use_real_data:
            raise ValueError("require_real_data requires use_real_data=True")

        # Load training data once
        if use_real_data:
            self.X, self.y_threat, self.y_attack = load_real_data(
                client_id=client_id,
                seq_len=_MODEL_CFG["sequence_len"],
                allow_synthetic_fallback=not require_real_data,
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

        # Training objects are initialized lazily on the first fit call because
        # the server supplies the actual federation round count in fit config.
        self._optimizer: Optional[torch.optim.Optimizer] = None
        self._train_loader = None
        self._privacy_engine = None
        self._using_opacus = False

        # Drift state (for cosine similarity tracking)
        self._prev_grad_flat: Optional[np.ndarray] = None
        self._prev_loss: Optional[float] = None

    # -----------------------------------------------------------------------
    # Flower API
    # -----------------------------------------------------------------------

    def _setup_training(self, config: dict) -> None:
        """Create one persistent optimizer/private engine for all FL rounds."""
        if self._optimizer is not None:
            return

        dataset = TensorDataset(self.X, self.y_threat, self.y_attack)
        loader = DataLoader(
            dataset,
            batch_size=_TRAIN_CFG["batch_size"],
            shuffle=True,
        )

        if self._dp_enabled:
            try:
                from opacus.validators import ModuleValidator
            except ImportError as exc:
                raise RuntimeError(
                    "Client-side DP was requested, but Opacus is not installed. "
                    "Install requirements.txt or disable differential_privacy.client_side."
                ) from exc

            if not ModuleValidator.is_valid(self.model):
                self.model = ModuleValidator.fix(self.model).to(self.device)

        optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=_TRAIN_CFG["learning_rate"],
        )

        if self._dp_enabled:
            from fl.privacy import make_opacus_private_engine

            dp_cfg = _DP_CFG.get("client_side", {})
            total_rounds = max(
                1,
                int(config.get("total_rounds", _FED_CFG["num_rounds"])),
            )
            (
                self.model,
                self._optimizer,
                self._train_loader,
                self._privacy_engine,
                noise_multiplier,
            ) = make_opacus_private_engine(
                model=self.model,
                optimizer=optimizer,
                data_loader=loader,
                target_epsilon=dp_cfg.get("epsilon", 5.0),
                target_delta=dp_cfg.get("delta", 1e-5),
                max_grad_norm=dp_cfg.get("max_grad_norm", 1.0),
                epochs=_TRAIN_CFG["local_epochs"] * total_rounds,
            )
            self._using_opacus = True
            logger.info(
                "[%s] Opacus DP-SGD activated for %d expected FL rounds (sigma=%.4f)",
                self.client_id,
                total_rounds,
                noise_multiplier,
            )
        else:
            self._optimizer = optimizer
            self._train_loader = loader

    def get_parameters(self, config: dict) -> List[np.ndarray]:
        return get_model_weights(self.model)

    def fit(
        self, parameters: List[np.ndarray], config: dict
    ) -> Tuple[List[np.ndarray], int, dict]:
        """
        Local training pipeline (FLARE v2):
          1. Set global model weights
          2. Check concept drift
          3. (On the first private round) initialize persistent Opacus DP-SGD
          4. Train for local_epochs
          5. (If personalization enabled) run pFedMe adaptation
          6. (If compression enabled) simulate lossy weight-delta compression
          7. Report multi-signal drift + privacy metrics
        """
        set_model_weights(self.model, parameters)
        self._setup_training(config)

        # Step 2: Concept drift check
        drift = check_concept_drift(self._rssi_values)

        # Step 3: DP-SGD setup
        dp_client_cfg = _DP_CFG.get("client_side", {})
        dp_eps, dp_delta = None, None
        optimizer = self._optimizer
        loader = self._train_loader
        if optimizer is None or loader is None:
            raise RuntimeError("Training components were not initialized")

        # Reset Adam momentum between federated rounds while retaining the
        # Opacus accountant and privacy hooks across the full federation.
        optimizer.state.clear()
        bce_loss = nn.BCELoss()
        ce_loss = nn.CrossEntropyLoss()
        confidence_loss = nn.MSELoss()
        w_threat = _TRAIN_CFG["loss_weight_threat"]
        w_attack = _TRAIN_CFG["loss_weight_attack"]
        w_confidence = _TRAIN_CFG.get("loss_weight_confidence", 0.2)

        # Step 4: Local training loop
        self.model.train()
        epoch_loss = 0.0
        n_batches = 0
        for epoch in range(_TRAIN_CFG["local_epochs"]):
            for xb, yb_threat, yb_attack in loader:
                xb = xb.to(self.device)
                yb_threat = yb_threat.to(self.device)
                yb_attack = yb_attack.to(self.device)

                out = self.model(xb)
                loss_threat = bce_loss(out.path_scores, yb_threat)
                loss_attack = ce_loss(out.attack_logits, yb_attack)
                confidence_target = confidence_correctness_target(
                    out,
                    yb_threat,
                    yb_attack,
                )
                loss_confidence = confidence_loss(
                    out.confidence.squeeze(-1),
                    confidence_target,
                )
                loss = (
                    w_threat * loss_threat
                    + w_attack * loss_attack
                    + w_confidence * loss_confidence
                )

                optimizer.zero_grad()
                loss.backward()

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
        if self._privacy_engine is not None:
            dp_delta = dp_client_cfg.get("delta", 1e-5)
            dp_eps = self._privacy_engine.get_epsilon(delta=dp_delta)
            logger.info(
                "[%s] Cumulative Opacus budget: ε=%.4f, δ=%.2e",
                self.client_id,
                dp_eps,
                dp_delta,
            )

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
            "dp_enabled": int(self._using_opacus),
            # Compression metrics
            "compression_ratio": compression_stats.get("compression_ratio", 1.0),
            "bytes_saved": compression_stats.get("original_bytes", 0) - compression_stats.get("compressed_bytes", 0),
            "original_bytes": compression_stats.get("original_bytes", 0),
            "compressed_bytes": compression_stats.get("compressed_bytes", 0),
            "wire_compression_applied": 0,
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
    parser.add_argument(
        "--require-real-data",
        action="store_true",
        help="Fail startup instead of falling back when real data is missing or invalid",
    )
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
        require_real_data=args.require_real_data,
    )
    fl.client.start_numpy_client(server_address=args.server_address, client=client)


if __name__ == "__main__":
    main()
