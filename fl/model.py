"""
fl/model.py — [REAL]
BiLSTM + Attention model for RF jamming threat detection.

Outputs (ThreatOutput named tuple):
  - path_scores   : Tensor[3]  — per-path threat probability (0=safe, 1=jammed)
  - confidence    : Tensor[1]  — prediction confidence (0–1)
  - attack_logits : Tensor[5]  — raw logits for attack type classification
                                 (none, barrage, sweep, spot, unknown)

Input shape: [batch, sequence_len, num_features]
"""

from collections import namedtuple
from typing import Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

# Named tuple for structured model output
ThreatOutput = namedtuple("ThreatOutput", ["path_scores", "confidence", "attack_logits"])

ATTACK_CLASSES = ["none", "barrage", "sweep", "spot", "unknown"]


def confidence_correctness_target(
    output: ThreatOutput,
    y_threat: torch.Tensor,
    y_attack: torch.Tensor,
) -> torch.Tensor:
    """Build a detached [0, 1] target from current threat/attack correctness."""
    with torch.no_grad():
        threat_correct = (
            (output.path_scores >= 0.5) == (y_threat >= 0.5)
        ).float().mean(dim=1)
        attack_correct = (
            output.attack_logits.argmax(dim=1) == y_attack
        ).float()
        return 0.5 * (threat_correct + attack_correct)


class AttentionPool(nn.Module):
    """Additive (Bahdanau-style) attention over the time dimension."""

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attn = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [B, T, H]
        scores = self.attn(x).squeeze(-1)          # [B, T]
        weights = F.softmax(scores, dim=-1)         # [B, T]
        context = (weights.unsqueeze(-1) * x).sum(dim=1)  # [B, H]
        return context


class BiLSTMAttention(nn.Module):
    """
    Bidirectional LSTM with attention pooling.
    Three output heads:
      A) path_scores   — sigmoid, shape [B, num_paths]
      B) confidence    — sigmoid, shape [B, 1]
      C) attack_logits — raw logits, shape [B, num_attack_classes]
    """

    def __init__(
        self,
        input_features: int = 5,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
        num_paths: int = 3,
        num_attack_classes: int = 5,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        # Bidirectional LSTM — output dim = hidden_size * 2
        self.lstm = nn.LSTM(
            input_size=input_features,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        lstm_out_dim = hidden_size * 2  # bidirectional

        self.attention = AttentionPool(lstm_out_dim)

        # Shared feature extractor after attention pooling
        self.shared = nn.Sequential(
            nn.Linear(lstm_out_dim, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

        # Head A: per-path threat scores
        self.head_threat = nn.Linear(64, num_paths)

        # Head B: confidence score
        self.head_confidence = nn.Linear(64, 1)

        # Head C: attack type classification
        self.head_attack = nn.Linear(64, num_attack_classes)

    def forward(self, x: torch.Tensor) -> ThreatOutput:
        """
        Args:
            x: Tensor of shape [B, T, input_features]
        Returns:
            ThreatOutput with path_scores, confidence, attack_logits
        """
        lstm_out, _ = self.lstm(x)        # [B, T, hidden*2]
        context = self.attention(lstm_out) # [B, hidden*2]
        features = self.shared(context)    # [B, 64]

        path_scores = torch.sigmoid(self.head_threat(features))      # [B, 3]
        confidence = torch.sigmoid(self.head_confidence(features))   # [B, 1]
        attack_logits = self.head_attack(features)                    # [B, 5]

        return ThreatOutput(
            path_scores=path_scores,
            confidence=confidence,
            attack_logits=attack_logits,
        )

    def predict(self, x: torch.Tensor) -> dict:
        """
        Convenience method for inference (no_grad).
        Returns a dict with numpy arrays and the attack class label.
        """
        self.eval()
        with torch.no_grad():
            out = self.forward(x)
            attack_idx = int(out.attack_logits.argmax(dim=-1).item())
            # The confidence head is trained to estimate current prediction correctness.
            conf_val = float(out.confidence.squeeze().item())
            return {
                "path_scores": out.path_scores.squeeze().tolist(),
                "confidence": conf_val,
                "attack_type": ATTACK_CLASSES[attack_idx],
                "attack_logits": out.attack_logits.squeeze().tolist(),
            }


# ---------------------------------------------------------------------------
# Weight serialisation helpers used by Flower FL client/server
# ---------------------------------------------------------------------------

def _federated_state(model: nn.Module) -> dict:
    """Canonical checkpoint order, independent of Opacus wrappers/aliases."""
    while isinstance(getattr(model, "_module", None), nn.Module):
        model = model._module
    state = model.state_dict()
    if isinstance(model, BiLSTMAttention):
        # DPLSTM registers ih bias before hh weight and also exposes cell aliases.
        # Both must serialize exactly like the original torch.nn.LSTM contract.
        keys = [
            f"lstm.{parameter}_l{layer}{direction}"
            for layer in range(model.lstm.num_layers)
            for direction in (("", "_reverse") if model.lstm.bidirectional else ("",))
            for parameter in ("weight_ih", "weight_hh", "bias_ih", "bias_hh")
        ]
        keys.extend(key for key in state if not key.startswith("lstm."))
        return {key: state[key] for key in keys}
    return state


def get_model_weights(model: nn.Module) -> list:
    """Return independent arrays in the established Flower parameter order."""
    return [value.detach().cpu().numpy().copy() for value in _federated_state(model).values()]


def set_model_weights(model: nn.Module, weights: list) -> None:
    """Validate then copy canonical parameters, including shared Opacus aliases."""
    state = _federated_state(model)
    if len(weights) != len(state):
        raise ValueError(f"Expected {len(state)} parameter arrays, received {len(weights)}")
    tensors = []
    for (key, target), value in zip(state.items(), weights):
        tensor = torch.as_tensor(value, device=target.device, dtype=target.dtype)
        if tensor.shape != target.shape:
            raise ValueError(f"Parameter {key} requires shape {tuple(target.shape)}, received {tuple(tensor.shape)}")
        tensors.append((target, tensor))
    # state_dict tensors reference the underlying parameters. Copying each once
    # updates DPLSTM's duplicate aliases without loading stale alias values last.
    with torch.no_grad():
        for target, tensor in tensors:
            target.copy_(tensor)


def build_model(cfg: dict) -> BiLSTMAttention:
    """Construct model from fl_config.yaml model section."""
    return BiLSTMAttention(
        input_features=cfg.get("input_features", 5),
        hidden_size=cfg.get("hidden_size", 64),
        num_layers=cfg.get("num_layers", 2),
        dropout=cfg.get("dropout", 0.2),
        num_paths=cfg.get("num_paths", 3),
        num_attack_classes=cfg.get("num_attack_classes", 5),
    )
