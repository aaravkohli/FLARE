"""
fl/__init__.py — FLARE v2 Federated Learning Package

Public API:
    from fl.model import build_model, get_model_weights, set_model_weights
    from fl.aggregator import secure_aggregate
    from fl.privacy import PrivacyAccountant, add_server_side_dp_noise
    from fl.compression import GradientCompressor
    from fl.trust import TrustRegistry, jains_fairness_index
    from fl.drift import DriftRegistry
    from fl.selection import AdaptiveClientSelector
    from fl.personalization import PersonalizationManager
    from fl.async_fl import AsyncFLBuffer
    from fl.distillation import DistillationController
    from fl.metrics import FLMetricsTracker
"""

__version__ = "2.0.0"
__all__ = [
    # Core (v1, backward compatible)
    "build_model", "get_model_weights", "set_model_weights",
    "secure_aggregate",
    # Privacy (v2)
    "PrivacyAccountant", "add_server_side_dp_noise",
    # Compression (v2)
    "GradientCompressor",
    # Trust (v2)
    "TrustRegistry", "jains_fairness_index",
    # Drift (v2)
    "DriftRegistry",
    # Selection (v2)
    "AdaptiveClientSelector",
    # Personalization (v2)
    "PersonalizationManager",
    # Async FL (v2)
    "AsyncFLBuffer",
    # Distillation (v2)
    "DistillationController",
    # Metrics (v2)
    "FLMetricsTracker",
]
