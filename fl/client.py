"""
fl/client.py — [REAL]
Flower federated learning client running on each simulated drone node.

Responsibilities:
  - Load local synthetic (or real) RF data
  - Train the BiLSTM model for local_epochs
  - Detect concept drift via KS test on RSSI distribution
  - Send weight updates to FL server (never raw data)

Usage:
  python fl/client.py --client_id drone_1 --server_address 127.0.0.1:8090
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Tuple, Dict

# Ensure project root is on sys.path when running as `python fl/client.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from scipy.stats import ks_2samp
import flwr as fl
import yaml

from fl.model import BiLSTMAttention, build_model, get_model_weights, set_model_weights

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
_FL_CFG = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_MODEL_CFG = _FL_CFG["model"]
_TRAIN_CFG = _FL_CFG["training"]
_DRIFT_CFG = _FL_CFG["drift_detection"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLIENT] %(message)s")
logger = logging.getLogger(__name__)

ATTACK_CLASSES = ["none", "barrage", "sweep", "spot", "unknown"]


# ---------------------------------------------------------------------------
# Synthetic data loader (replaced with real sensor data in REAL mode)
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

    # Feature order: rssi, pdr, sinr, latency, packet_loss
    X_list, y_threat_list, y_attack_list = [], [], []

    for _ in range(num_samples):
        # Randomly decide which paths (if any) are jammed this sample
        jammed_paths = rng.choice([True, False], size=3, p=[0.3, 0.7])
        if jammed:
            jammed_paths = np.array([True, False, False])  # path 0 always jammed

        seq = []
        for t in range(seq_len):
            step = []
            for path_jammed in jammed_paths:
                if path_jammed:
                    # Degraded RF metrics under jamming
                    rssi = rng.uniform(-110, -85)
                    pdr = rng.uniform(0.0, 0.4)
                    sinr = rng.uniform(-5, 5)
                    latency = rng.uniform(200, 800)
                    packet_loss = rng.uniform(0.3, 0.9)
                else:
                    # Normal RF metrics
                    rssi = rng.uniform(-75, -40)
                    pdr = rng.uniform(0.7, 1.0)
                    sinr = rng.uniform(10, 30)
                    latency = rng.uniform(5, 80)
                    packet_loss = rng.uniform(0.0, 0.1)
                step.extend([rssi, pdr, sinr, latency, packet_loss])
            seq.append(step)

        # Take one path's features per timestep (simplified: use path-0 features)
        # In practice each drone monitors all paths; here we flatten path 0
        seq_arr = np.array(seq)[:, :5].astype(np.float32)

        # Normalise
        norms = np.array([-40.0, 1.0, 30.0, 800.0, 1.0])
        mins  = np.array([-120.0, 0.0, -10.0, 0.0, 0.0])
        seq_arr = (seq_arr - mins) / (norms - mins + 1e-8)

        X_list.append(seq_arr)
        y_threat_list.append(jammed_paths.astype(np.float32))

        # Assign attack label: if any path jammed, pick a random attack type
        if jammed_paths.any():
            attack_idx = rng.integers(1, 5)  # 1–4 (barrage, sweep, spot, unknown)
        else:
            attack_idx = 0
        y_attack_list.append(attack_idx)

    X = torch.tensor(np.stack(X_list), dtype=torch.float32)          # [N, T, 5]
    y_threat = torch.tensor(np.stack(y_threat_list), dtype=torch.float32)  # [N, 3]
    y_attack = torch.tensor(np.array(y_attack_list), dtype=torch.long)     # [N]
    return X, y_threat, y_attack


# ---------------------------------------------------------------------------
# Concept Drift Detection
# ---------------------------------------------------------------------------

_rssi_baseline: np.ndarray | None = None


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
        logger.warning("Concept drift detected (KS p=%.4f < %.4f). Resetting RSSI baseline.", p_value, p_threshold)
        _rssi_baseline = current_rssi.copy()
    return drift


# ---------------------------------------------------------------------------
# Flower Client
# ---------------------------------------------------------------------------

class DroneFlClient(fl.client.NumPyClient):
    """
    Flower NumPyClient for a single drone node.
    Trains the BiLSTM model locally and returns weight updates.
    """

    def __init__(self, client_id: str, jammed: bool = False):
        self.client_id = client_id
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = build_model(_MODEL_CFG).to(self.device)
        self.jammed = jammed

        # Load training data once
        self.X, self.y_threat, self.y_attack = load_local_data(
            client_id=client_id,
            seq_len=_MODEL_CFG["sequence_len"],
            num_samples=300,
            jammed=jammed,
        )
        logger.info("Client %s loaded %d samples (jammed=%s)", client_id, len(self.X), jammed)

        # Concept drift: track RSSI across rounds
        self._rssi_values = self.X[:, :, 0].numpy().flatten()  # channel 0 = RSSI

    def get_parameters(self, config: dict) -> List[np.ndarray]:
        return get_model_weights(self.model)

    def fit(
        self, parameters: List[np.ndarray], config: dict
    ) -> Tuple[List[np.ndarray], int, dict]:
        """Local training for local_epochs on this drone's data."""
        set_model_weights(self.model, parameters)

        # Check for concept drift before training
        drift = check_concept_drift(self._rssi_values)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=_TRAIN_CFG["learning_rate"])
        bce_loss = nn.BCELoss()
        ce_loss = nn.CrossEntropyLoss()

        w_threat = _TRAIN_CFG["loss_weight_threat"]
        w_attack = _TRAIN_CFG["loss_weight_attack"]

        dataset = TensorDataset(self.X, self.y_threat, self.y_attack)
        loader = DataLoader(dataset, batch_size=_TRAIN_CFG["batch_size"], shuffle=True)

        self.model.train()
        for epoch in range(_TRAIN_CFG["local_epochs"]):
            epoch_loss = 0.0
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
                optimizer.step()
                epoch_loss += loss.item()

            logger.info("  [%s] Epoch %d loss=%.4f", self.client_id, epoch + 1, epoch_loss / len(loader))

        metrics = {
            "train_loss": float(epoch_loss / len(loader)),
            "concept_drift": int(drift),
            "client_id": self.client_id,
        }
        return get_model_weights(self.model), len(self.X), metrics

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

        avg_loss = total_loss / len(loader)
        accuracy = correct_threat / total
        logger.info("  [%s] Eval loss=%.4f acc=%.4f", self.client_id, avg_loss, accuracy)
        return avg_loss, total, {"accuracy": accuracy}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Drone FL Client")
    parser.add_argument("--client_id", type=str, default="drone_1")
    parser.add_argument("--server_address", type=str, default="127.0.0.1:8090")
    parser.add_argument("--jammed", action="store_true", help="Simulate jammed environment")
    args = parser.parse_args()

    logger.info("Starting FL client: %s → %s (jammed=%s)", args.client_id, args.server_address, args.jammed)
    client = DroneFlClient(client_id=args.client_id, jammed=args.jammed)
    fl.client.start_numpy_client(server_address=args.server_address, client=client)


if __name__ == "__main__":
    main()
