"""
fl/server.py — [REAL]
Flower FL server with secure aggregation and poisoning detection.

Responsibilities:
  - Coordinate FL rounds using FedAvg + secure aggregation
  - Detect and exclude poisoned client updates
  - Save the global model after each round to models/fl_model.pth
  - Broadcast updated global model to all clients

Usage:
  python fl/server.py [--rounds 20]
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

# Ensure project root is on sys.path when running as `python fl/server.py`
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
from fl.model import build_model, set_model_weights

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent.parent
_FL_CFG = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_MODEL_CFG = _FL_CFG["model"]
_FED_CFG = _FL_CFG["federation"]
_SEC_CFG = _FL_CFG["security"]
_PATHS_CFG = _FL_CFG["paths"]

os.makedirs(_BASE / "models", exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVER] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom Strategy: SecureFedAvg
# ---------------------------------------------------------------------------

class SecureFedAvg(fl.server.strategy.FedAvg):
    """
    Extends Flower's FedAvg with:
      - Secure aggregation (clip + anomaly filter + trimmed mean)
      - Saves global model to disk after each round
      - Logs audit info per round
    """

    def __init__(self, initial_parameters: Parameters, **kwargs):
        super().__init__(initial_parameters=initial_parameters, **kwargs)
        self._global_weights: Optional[List[np.ndarray]] = None

    def aggregate_fit(
        self,
        server_round: int,
        results: List[Tuple[ClientProxy, FitRes]],
        failures: List[Union[Tuple[ClientProxy, FitRes], BaseException]],
    ) -> Tuple[Optional[Parameters], Dict[str, Scalar]]:
        """Override aggregation to inject secure_aggregate."""
        if not results:
            logger.warning("Round %d: no client results received.", server_round)
            return None, {}

        # Extract weights and sample counts from Flower results
        client_weights: List[List[np.ndarray]] = []
        num_examples: List[int] = []
        for _, fit_res in results:
            client_weights.append(parameters_to_ndarrays(fit_res.parameters))
            num_examples.append(fit_res.num_examples)

        # Use current global weights as baseline for clipping
        baseline = self._global_weights if self._global_weights else client_weights[0]

        # Secure aggregation
        aggregated, audit = secure_aggregate(
            client_weights=client_weights,
            global_weights=baseline,
            num_examples=num_examples,
            clip_norm=_SEC_CFG["clip_norm"],
            z_threshold=_SEC_CFG["anomaly_z_threshold"],
            use_trimmed_mean=_SEC_CFG["use_trimmed_mean"],
            trim_ratio=_SEC_CFG["trim_ratio"],
        )

        self._global_weights = aggregated

        # Persist global model to disk
        self._save_model(aggregated)

        metrics: Dict[str, Scalar] = {
            "round": server_round,
            "clients_used": audit["used"],
            "clients_excluded": len(audit["excluded"]),
            "aggregation_method": audit["method"],
        }
        logger.info(
            "Round %d complete | used=%d/%d | excluded=%s | method=%s",
            server_round,
            audit["used"],
            audit["total_clients"],
            audit["excluded"],
            audit["method"],
        )

        return ndarrays_to_parameters(aggregated), metrics

    def _save_model(self, weights: List[np.ndarray]) -> None:
        """Save global model weights to fl_model.pth."""
        model = build_model(_MODEL_CFG)
        set_model_weights(model, weights)
        save_path = _BASE / _PATHS_CFG["model_save"]
        torch.save(model.state_dict(), save_path)
        logger.info("Global model saved → %s", save_path)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def run_server(num_rounds: int = _FED_CFG["num_rounds"]):
    # Initialise model and get starting parameters
    model = build_model(_MODEL_CFG)
    initial_weights = [val.cpu().numpy() for val in model.state_dict().values()]
    initial_params = ndarrays_to_parameters(initial_weights)

    strategy = SecureFedAvg(
        initial_parameters=initial_params,
        min_fit_clients=_FED_CFG["min_clients"],
        min_evaluate_clients=_FED_CFG["min_clients"],
        min_available_clients=_FED_CFG["min_clients"],
        fraction_fit=_FED_CFG["fraction_fit"],
        fraction_evaluate=1.0,
    )

    logger.info("Starting FL server on %s for %d rounds.", _FED_CFG["server_address"], num_rounds)
    fl.server.start_server(
        server_address=_FED_CFG["server_address"],
        config=fl.server.ServerConfig(num_rounds=num_rounds),
        strategy=strategy,
    )


def main():
    parser = argparse.ArgumentParser(description="FL Aggregation Server")
    parser.add_argument("--rounds", type=int, default=_FED_CFG["num_rounds"])
    parser.add_argument("--retrain", action="store_true", help="Retraining mode (lifecycle)")
    args = parser.parse_args()
    run_server(num_rounds=args.rounds)


if __name__ == "__main__":
    main()
