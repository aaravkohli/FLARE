"""Regression coverage for Phase 3 FL/ML correctness improvements."""

from __future__ import annotations

import numpy as np
import pytest
import torch

import fl.client as fl_client
from fl.aggregator import clip_weights_by_norm, trimmed_mean
from fl.client import DroneFlClient, load_local_data
from fl.compression import GradientCompressor
from fl.model import ThreatOutput, confidence_correctness_target, get_model_weights


def test_synthetic_client_data_is_reproducible_and_path_aligned():
    first = load_local_data("drone_1", seq_len=4, num_samples=24)
    second = load_local_data("drone_1", seq_len=4, num_samples=24)

    for first_tensor, second_tensor in zip(first, second):
        assert torch.equal(first_tensor, second_tensor)

    features, threat_labels, attack_labels = first
    assert features.shape == (24, 4, 5)
    assert threat_labels.shape == (24, 3)
    assert torch.equal(threat_labels[:, 0], threat_labels[:, 1])
    assert torch.equal(threat_labels[:, 1], threat_labels[:, 2])
    assert torch.equal(attack_labels == 0, threat_labels[:, 0] == 0)


def test_forced_jammed_synthetic_data_has_consistent_labels():
    _, threat_labels, attack_labels = load_local_data(
        "drone_2",
        seq_len=3,
        num_samples=12,
        jammed=True,
    )
    assert torch.all(threat_labels == 1)
    assert torch.all(attack_labels > 0)


def test_confidence_target_represents_current_prediction_correctness():
    output = ThreatOutput(
        path_scores=torch.tensor([[0.9, 0.2, 0.8]]),
        confidence=torch.tensor([[0.5]]),
        attack_logits=torch.tensor([[0.1, 0.2, 2.0, 0.0, -1.0]]),
    )
    target = confidence_correctness_target(
        output,
        y_threat=torch.tensor([[1.0, 0.0, 0.0]]),
        y_attack=torch.tensor([2]),
    )
    assert target.item() == pytest.approx(5.0 / 6.0)
    assert not target.requires_grad


def test_confidence_head_receives_training_updates(monkeypatch):
    monkeypatch.setitem(fl_client._TRAIN_CFG, "local_epochs", 1)
    monkeypatch.setitem(fl_client._TRAIN_CFG, "batch_size", 8)
    client = DroneFlClient(
        "drone_3",
        enable_dp=False,
        enable_compression=False,
        enable_personalization=False,
    )
    client.X = client.X[:8]
    client.y_threat = client.y_threat[:8]
    client.y_attack = client.y_attack[:8]
    parameters = get_model_weights(client.model)
    before = client.model.head_confidence.weight.detach().clone()

    _, num_examples, metrics = client.fit(parameters, {"total_rounds": 1})

    after = client.model.head_confidence.weight.detach()
    assert num_examples == 8
    assert metrics["dp_enabled"] == 0
    assert not torch.equal(before, after)


def test_client_update_clipping_uses_one_global_l2_norm():
    baseline = [np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32)]
    update = [np.array([3.0], dtype=np.float32), np.array([4.0], dtype=np.float32)]

    clipped = clip_weights_by_norm(update, baseline, max_norm=2.5)
    global_norm = np.sqrt(sum(float(np.sum(layer**2)) for layer in clipped))

    assert global_norm == pytest.approx(2.5)
    assert clipped[0].item() == pytest.approx(1.5)
    assert clipped[1].item() == pytest.approx(2.0)


def test_trimmed_mean_respects_configured_fraction():
    weights = [
        [np.array([0.0], dtype=np.float32)],
        [np.array([1.0], dtype=np.float32)],
        [np.array([100.0], dtype=np.float32)],
    ]
    result = trimmed_mean(weights, trim_ratio=0.1)
    assert result[0].item() == pytest.approx(101.0 / 3.0)


def test_compression_metrics_are_labeled_as_simulated_wire_savings():
    compressor = GradientCompressor(
        strategy="topk",
        topk_ratio=0.1,
        error_feedback=False,
    )
    weights = [np.linspace(-1.0, 1.0, 1000, dtype=np.float32)]
    payload, metadata = compressor.compress(weights)
    reconstructed = compressor.decompress(payload, metadata)
    stats = compressor.get_round_stats()

    assert reconstructed[0].shape == weights[0].shape
    assert stats["compressed_bytes"] < stats["original_bytes"]
    assert stats["wire_compression_applied"] is False

