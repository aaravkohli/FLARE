"""Tests for FLARE's trust-weighted Byzantine-resilient FL layer."""

from __future__ import annotations

import json

import numpy as np

from fl.aggregator import (
    TrustWeightedAggregationStrategy,
    detect_byzantine_updates,
    fedavg,
    run_shadow_protection_self_test,
)
from fl.client import simulate_model_update_attack
from fl.metrics import FLMetricsTracker
from fl.trust import ClientIdentityRegistry, TrustManager, UpdateAnalyzer
from simulation.byzantine_state import get_attack_mode, reset_attack_state, set_attack_mode
from scripts.run_byzantine_experiment import run_seed_study


def _updates():
    baseline = [np.zeros(4, dtype=np.float64)]
    clients = [
        [np.array([1.00, 0.95, 1.05, 1.00])],
        [np.array([0.95, 1.00, 1.00, 1.05])],
        [np.array([1.05, 1.00, 0.95, 1.00])],
        [np.array([-20.0, -20.0, -20.0, -20.0])],
    ]
    return baseline, clients


def test_robust_analysis_detects_strong_poison_without_flagging_majority():
    baseline, clients = _updates()
    audits = detect_byzantine_updates(
        clients,
        baseline,
        ["drone_1", "drone_2", "drone_3", "drone_bad"],
    )

    assert [row["status"] for row in audits[:3]] == ["NORMAL", "NORMAL", "NORMAL"]
    assert audits[-1]["status"] == "MALICIOUS"
    assert audits[-1]["evidence_count"] >= 2
    assert audits[-1]["cosine_similarity"] < 0.0
    assert 0.0 <= audits[-1]["deviation_score"] <= 1.0


def test_trust_weighted_strategy_rejects_poison_and_limits_model_damage():
    baseline, clients = _updates()
    ids = ["drone_1", "drone_2", "drone_3", "drone_bad"]
    strategy = TrustWeightedAggregationStrategy(clip_norm=100.0)

    secure, audit = strategy.aggregate(ids, clients, baseline, [10, 10, 10, 10])
    insecure = fedavg(clients, [10, 10, 10, 10])

    malicious = next(row for row in audit["clients"] if row["client_id"] == "drone_bad")
    assert malicious["action"] == "REJECTED"
    assert malicious["aggregation_weight"] == 0.0
    assert audit["rejected_updates"] == 1
    assert np.linalg.norm(secure[0] - np.ones(4)) < 0.1
    assert np.linalg.norm(secure[0] - np.ones(4)) < np.linalg.norm(insecure[0] - np.ones(4))


def test_shadow_protection_self_test_rejects_candidate_without_live_mutation():
    result = run_shadow_protection_self_test()

    malicious = next(
        row
        for row in result["clients"]
        if row["client_id"] == result["malicious_candidate"]
    )
    assert result["passed"] is True
    assert result["mode"] == "isolated_shadow_test"
    assert result["global_model_modified"] is False
    assert result["trust_history_modified"] is False
    assert result["client_modes_modified"] is False
    assert malicious["status"] == "MALICIOUS"
    assert malicious["action"] == "REJECTED"
    assert malicious["aggregation_weight"] == 0.0
    assert result["secure_aggregate_distance"] < result["attacked_fedavg_distance"]


def test_non_finite_update_is_rejected_without_aborting_the_round():
    baseline, clients = _updates()
    clients[-1] = [np.array([np.nan, 0.0, 0.0, 0.0])]
    strategy = TrustWeightedAggregationStrategy(clip_norm=100.0)

    aggregated, audit = strategy.aggregate(
        ["drone_1", "drone_2", "drone_3", "drone_bad"],
        clients,
        baseline,
        [1, 1, 1, 1],
    )

    invalid = audit["clients"][-1]
    assert invalid["action"] == "REJECTED"
    assert "invalid update" in invalid["rejection_reason"]
    assert np.all(np.isfinite(aggregated[0]))


def test_repeated_poisoning_reduces_historical_trust():
    baseline, clients = _updates()
    manager = TrustManager(history_alpha=0.5, initial_trust=0.8)
    strategy = TrustWeightedAggregationStrategy(trust_manager=manager, clip_norm=100.0)
    ids = ["drone_1", "drone_2", "drone_3", "drone_bad"]

    first_trust = None
    last_audit = None
    for _ in range(4):
        _, last_audit = strategy.aggregate(ids, clients, baseline, [1, 1, 1, 1])
        bad = next(row for row in last_audit["clients"] if row["client_id"] == "drone_bad")
        first_trust = bad["trust_score"] if first_trust is None else first_trust

    bad = next(row for row in last_audit["clients"] if row["client_id"] == "drone_bad")
    benign = [row["trust_score"] for row in last_audit["clients"] if row["client_id"] != "drone_bad"]
    assert bad["trust_score"] < first_trust
    assert bad["trust_score"] < min(benign)
    assert manager.summary()["total_poisoning_attempts_detected"] == 4


def test_trust_history_survives_server_restart(tmp_path):
    baseline, clients = _updates()
    state_path = tmp_path / "trust-state.json"
    ids = ["drone_1", "drone_2", "drone_3", "drone_bad"]
    first = TrustManager(history_alpha=0.5, state_path=str(state_path))
    TrustWeightedAggregationStrategy(trust_manager=first, clip_norm=100.0).aggregate(
        ids, clients, baseline, [1, 1, 1, 1]
    )
    prior_trust = first.get_trust("drone_bad")

    restored = TrustManager(history_alpha=0.5, state_path=str(state_path))
    assert restored.get_trust("drone_bad") == prior_trust
    assert len(restored.historical_norms()["drone_bad"]) == 1
    assert restored.summary()["total_poisoning_attempts_detected"] == 1


def test_reported_sample_count_is_robustly_capped():
    baseline, clients = _updates()
    strategy = TrustWeightedAggregationStrategy(clip_norm=100.0)
    _, audit = strategy.aggregate(
        ["drone_1", "drone_2", "drone_3", "drone_bad"],
        clients,
        baseline,
        [1_000_000_000, 10, 10, 10],
    )
    inflated = audit["clients"][0]
    assert audit["sample_count_capped_clients"] == 1
    assert inflated["effective_num_examples"] <= 30
    assert inflated["aggregation_weight"] < 0.7


def test_invalid_sample_count_is_rejected_without_aborting_round():
    baseline = [np.zeros(1)]
    clients = [[np.array([1.0])], [np.array([1.0])], [np.array([-500.0])]]
    strategy = TrustWeightedAggregationStrategy(clip_norm=1000.0)

    aggregated, audit = strategy.aggregate(
        ["drone_1", "drone_2", "drone_bad"], clients, baseline, [10, 10, -1]
    )

    assert np.allclose(aggregated[0], [1.0])
    assert audit["clients"][2]["action"] == "REJECTED"
    assert "sample count" in audit["clients"][2]["rejection_reason"]


def test_adaptive_clipping_uses_majority_scale_below_fixed_ceiling():
    baseline = [np.zeros(1)]
    clients = [[np.array([1.0])], [np.array([1.0])], [np.array([10.0])]]
    strategy = TrustWeightedAggregationStrategy(
        analyzer=UpdateAnalyzer(min_clients=10),
        clip_norm=100.0,
        adaptive_clip_enabled=True,
        clip_mad_multiplier=3.0,
    )
    aggregated, audit = strategy.aggregate(
        ["drone_1", "drone_2", "drone_3"], clients, baseline, [1, 1, 1]
    )
    assert 1.0 < audit["effective_clip_norm"] < 2.0
    assert aggregated[0][0] < 1.2


def test_identity_registry_rejects_rotation_and_duplicate_claims():
    registry = ClientIdentityRegistry(["drone_1", "drone_2"])
    assert registry.resolve("proxy-a", "drone_1") == ("drone_1", None)
    _, rotation_error = registry.resolve("proxy-a", "drone_2")
    duplicate_id, duplicate_error = registry.resolve("proxy-b", "drone_1")
    _, unknown_error = registry.resolve("proxy-c", "drone_9")
    assert "changed identity" in rotation_error
    assert duplicate_id == "unverified:proxy-b"
    assert "duplicate identity" in duplicate_error
    assert "not allowed" in unknown_error

    registry.begin_round()
    assert registry.resolve("proxy-b", "drone_1") == ("drone_1", None)


def test_forced_identity_rejection_cannot_influence_aggregation():
    baseline = [np.zeros(1)]
    clients = [[np.array([1.0])], [np.array([1.0])], [np.array([1.0])]]
    strategy = TrustWeightedAggregationStrategy(clip_norm=100.0)
    aggregated, audit = strategy.aggregate(
        ["drone_1", "drone_2", "unverified:proxy-c"],
        clients,
        baseline,
        [1, 1, 1],
        forced_rejections={2: "duplicate identity"},
    )
    assert audit["clients"][2]["action"] == "REJECTED"
    assert audit["clients"][2]["aggregation_weight"] == 0.0
    assert np.allclose(aggregated[0], [1.0])


def test_coordinate_median_fallback_is_selected_when_suspicion_is_high():
    baseline, clients = _updates()
    strategy = TrustWeightedAggregationStrategy(
        suspicious_fraction_for_fallback=0.20,
        fallback_strategy="coordinate_median",
        clip_norm=100.0,
    )
    _, audit = strategy.aggregate(
        ["drone_1", "drone_2", "drone_3", "drone_bad"],
        clients,
        baseline,
        [1, 1, 1, 1],
    )
    assert audit["method"] == "coordinate_median_fallback"


def test_attack_simulator_supports_normal_noisy_and_strong_poisoned_updates():
    baseline = [np.zeros(3)]
    update = [np.ones(3)]
    normal = simulate_model_update_attack(update, baseline, "normal")
    noisy = simulate_model_update_attack(
        update, baseline, "noisy", rng=np.random.default_rng(7), noisy_std_multiplier=0.25
    )
    poisoned = simulate_model_update_attack(update, baseline, "poisoned", poison_scale=20.0)

    assert np.array_equal(normal[0], update[0])
    assert not np.array_equal(noisy[0], update[0])
    assert np.allclose(poisoned[0], -20.0)


def test_dynamic_attack_state_round_trip(tmp_path):
    state_path = tmp_path / "byzantine.json"
    set_attack_mode("drone_3", "poisoned", state_path)
    assert get_attack_mode("drone_3", path=state_path) == "poisoned"
    set_attack_mode("drone_3", "normal", state_path)
    assert get_attack_mode("drone_3", path=state_path) == "normal"


def test_attack_state_reset_restores_every_client_to_normal(tmp_path):
    state_path = tmp_path / "byzantine.json"
    set_attack_mode("drone_1", "poisoned", state_path)
    set_attack_mode("drone_2", "noisy", state_path)

    reset_attack_state(state_path)

    assert json.loads(state_path.read_text(encoding="utf-8")) == {}
    assert get_attack_mode("drone_1", path=state_path) == "normal"
    assert get_attack_mode("drone_2", path=state_path) == "normal"


def test_security_audit_is_written_to_metrics_snapshot(tmp_path):
    tracker = FLMetricsTracker(
        csv_path=str(tmp_path / "metrics.csv"),
        json_path=str(tmp_path / "metrics.json"),
    )
    tracker.start_round(3)
    tracker.update_security_audit({
        "suspected_malicious_clients": 1,
        "rejected_updates": 1,
        "down_weighted_updates": 0,
        "total_poisoning_attempts_detected": 2,
        "clients": [{"client_id": "drone_3", "trust_score": 0.12, "action": "REJECTED"}],
    })
    tracker.commit(3)

    snapshot = json.loads((tmp_path / "metrics.json").read_text())
    assert snapshot["latest"]["poisoning_attempts_detected"] == 2
    assert snapshot["latest"]["client_security"][0]["action"] == "REJECTED"


def test_multi_seed_experiment_reports_pooled_detection_quality():
    report = run_seed_study(rounds=2, seeds=[42])
    pooled = report["pooled_detection"]

    assert pooled["malicious_updates_submitted"] == 2
    assert pooled["malicious_updates_detected"] == 2
    assert pooled["malicious_updates_rejected"] == 2
    assert pooled["recall"] == 1.0
    assert 0.0 <= pooled["precision"] <= 1.0
    assert report["summary"]["secure_accuracy"]["mean"] > report["summary"]["attacked_fedavg_accuracy"]["mean"]
