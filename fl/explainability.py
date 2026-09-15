"""Target-specific Integrated Gradients explanations for FLARE models."""

from __future__ import annotations

import math
from typing import Literal, Optional

import numpy as np
import torch
import torch.nn.functional as F

from schemas.contracts import EXPLANATION_CONTRACT, THREAT_FEATURES


def _target_scalar(model, inputs: torch.Tensor, target_head: str, target_index: int):
    output = model(inputs)
    if target_head == "threat":
        if target_index < 0 or target_index >= output.path_scores.shape[-1]:
            raise ValueError("threat target_index is outside the path head")
        return output.path_scores[:, target_index]
    if target_head == "attack":
        if target_index < 0 or target_index >= output.attack_logits.shape[-1]:
            raise ValueError("attack target_index is outside the attack head")
        return F.softmax(output.attack_logits, dim=-1)[:, target_index]
    raise ValueError("target_head must be 'threat' or 'attack'")


def integrated_gradients(
    model,
    inputs: torch.Tensor,
    *,
    target_head: Literal["threat", "attack"] = "threat",
    target_index: int = 0,
    baseline: Optional[torch.Tensor] = None,
    steps: int = 32,
) -> dict:
    """Explain one [1,T,F] model input with the trapezoidal IG estimator."""
    if inputs.ndim != 3 or inputs.shape[0] != 1:
        raise ValueError("inputs must have shape [1, sequence_length, features]")
    if steps < 8:
        raise ValueError("steps must be at least 8")
    if baseline is None:
        baseline = torch.zeros_like(inputs)
    if baseline.shape != inputs.shape:
        raise ValueError("baseline shape must match inputs")
    model.eval()
    device = next(model.parameters()).device
    x = inputs.detach().to(device)
    base = baseline.detach().to(device)
    alphas = torch.linspace(0.0, 1.0, steps + 1, device=device)
    gradients = []
    for alpha in alphas:
        point = (base + alpha * (x - base)).detach().requires_grad_(True)
        scalar = _target_scalar(model, point, target_head, int(target_index)).sum()
        gradient = torch.autograd.grad(scalar, point, retain_graph=False)[0]
        gradients.append(gradient.detach())
    gradient_path = torch.stack(gradients)
    average_gradient = (
        gradient_path[0]
        + gradient_path[-1]
        + 2.0 * gradient_path[1:-1].sum(dim=0)
    ) / (2.0 * steps)
    attributions = (x - base) * average_gradient

    with torch.no_grad():
        prediction = float(_target_scalar(model, x, target_head, target_index).item())
        baseline_prediction = float(
            _target_scalar(model, base, target_head, target_index).item()
        )
    attribution_sum = float(attributions.sum().item())
    output_delta = prediction - baseline_prediction
    completeness_error = abs(output_delta - attribution_sum)
    normalized_error = completeness_error / max(abs(output_delta), 1e-8)
    attr = attributions.squeeze(0).cpu().numpy()
    feature_abs = np.abs(attr).sum(axis=0)
    total_abs = float(feature_abs.sum())
    importance = (
        feature_abs / total_abs if total_abs > 0.0 else np.zeros_like(feature_abs)
    )
    signed = attr.sum(axis=0)
    return {
        "method": "Integrated Gradients",
        "contract": EXPLANATION_CONTRACT,
        "target": {"head": target_head, "index": int(target_index)},
        "prediction": prediction,
        "baseline_prediction": baseline_prediction,
        "output_delta": output_delta,
        "attribution_sum": attribution_sum,
        "completeness_error": completeness_error,
        "normalized_completeness_error": normalized_error,
        "faithfulness_passed": bool(math.isfinite(normalized_error) and normalized_error <= 0.20),
        "steps": steps,
        "feature_attributions": [
            {
                "feature": name,
                "importance": float(importance[index]),
                "signed_attribution": float(signed[index]),
            }
            for index, name in enumerate(THREAT_FEATURES[: attr.shape[1]])
        ],
        "temporal_attributions": attr.tolist(),
    }


def integrated_gradients_q_values(
    q_network,
    observation: torch.Tensor,
    *,
    action_id: int,
    baseline: Optional[torch.Tensor] = None,
    steps: int = 32,
) -> dict:
    """Explain a selected DQN Q value; used by routing_state_v3 diagnostics."""
    if observation.ndim == 1:
        observation = observation.unsqueeze(0)
    if observation.ndim != 2 or observation.shape[0] != 1:
        raise ValueError("observation must have shape [features] or [1, features]")
    baseline = torch.zeros_like(observation) if baseline is None else baseline
    device = next(q_network.parameters()).device
    x, base = observation.to(device), baseline.to(device)
    alphas = torch.linspace(0.0, 1.0, steps + 1, device=device)
    gradients = []
    for alpha in alphas:
        point = (base + alpha * (x - base)).detach().requires_grad_(True)
        q_values = q_network(point)
        if action_id < 0 or action_id >= q_values.shape[-1]:
            raise ValueError("action_id is outside the Q-value output")
        gradients.append(torch.autograd.grad(q_values[:, action_id].sum(), point)[0])
    path = torch.stack(gradients)
    avg = (path[0] + path[-1] + 2.0 * path[1:-1].sum(0)) / (2.0 * steps)
    attr = ((x - base) * avg).squeeze(0).detach().cpu().numpy()
    with torch.no_grad():
        prediction = float(q_network(x)[0, action_id].item())
        baseline_prediction = float(q_network(base)[0, action_id].item())
    delta = prediction - baseline_prediction
    error = abs(delta - float(attr.sum()))
    return {
        "method": "Integrated Gradients",
        "contract": EXPLANATION_CONTRACT,
        "target": {"head": "q_value", "index": int(action_id)},
        "prediction": prediction,
        "baseline_prediction": baseline_prediction,
        "attributions": attr.tolist(),
        "normalized_completeness_error": error / max(abs(delta), 1e-8),
    }
