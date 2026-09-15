"""Reproducible FedAvg versus Byzantine-resilient aggregation experiment.

This lightweight experiment uses federated logistic updates so it can run in
seconds in CI while exercising FLARE's production aggregation implementation.
It writes a machine-readable report to ``results/byzantine_experiment.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from sklearn.metrics import accuracy_score, f1_score

sys.path.insert(0, str(Path(__file__).parent.parent))

from fl.aggregator import TrustWeightedAggregationStrategy, fedavg
from fl.client import simulate_model_update_attack
from fl.trust import TrustManager, UpdateAnalyzer
from provenance import promote_run, write_experiment_run

logging.getLogger("fl.aggregator").setLevel(logging.WARNING)


def _make_data(seed: int, n_clients: int = 5) -> Tuple[List[Tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    true_weights = np.array([1.8, -1.4, 1.1, 0.7, -0.9, 1.3], dtype=np.float64)
    features = rng.normal(size=(3000, len(true_weights)))
    logits = features @ true_weights + rng.normal(scale=0.35, size=len(features))
    labels = (logits > 0.0).astype(np.float64)
    order = rng.permutation(len(features))
    train_idx, test_idx = order[:2500], order[2500:]
    shards = np.array_split(train_idx, n_clients)
    clients = [(features[idx], labels[idx]) for idx in shards]
    return clients, features[test_idx], labels[test_idx]


def _local_update(weights: np.ndarray, features: np.ndarray, labels: np.ndarray, lr: float = 0.35) -> np.ndarray:
    local = weights.copy()
    for _ in range(3):
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(features @ local[:-1] + local[-1], -30, 30)))
        error = probabilities - labels
        gradient = np.concatenate([(features.T @ error) / len(features), [float(np.mean(error))]])
        local -= lr * gradient
    return local


def _evaluate(weights: np.ndarray, features: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    predictions = (features @ weights[:-1] + weights[-1] >= 0.0).astype(int)
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
    }


def _run(
    mode: str,
    clients: List[Tuple[np.ndarray, np.ndarray]],
    test_x: np.ndarray,
    test_y: np.ndarray,
    rounds: int,
    seed: int,
) -> dict:
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    global_weights = np.zeros(test_x.shape[1] + 1, dtype=np.float64)
    # Benchmark identities are deliberately not production fleet identities.
    # This prevents a five-client experiment cohort from being mistaken for
    # drones enrolled in config/fleet_registry.yaml.
    client_ids = [f"benchmark_client_{index + 1}" for index in range(len(clients))]
    malicious_id = client_ids[-1]
    noisy_id = client_ids[-2]
    defense = TrustWeightedAggregationStrategy(
        analyzer=UpdateAnalyzer(),
        trust_manager=TrustManager(),
        clip_norm=5.0,
        suspicious_fraction_for_fallback=0.5,
        fallback_strategy="coordinate_median",
    )
    true_detections = true_rejections = false_detections = false_rejections = 0
    latest_audit = None

    for round_number in range(1, rounds + 1):
        updates = []
        for index, (features, labels) in enumerate(clients):
            update = _local_update(global_weights, features, labels)
            attack_mode = "normal"
            if mode != "clean" and client_ids[index] == noisy_id:
                attack_mode = "noisy"
            if mode != "clean" and client_ids[index] == malicious_id:
                attack_mode = "poisoned"
            update = simulate_model_update_attack(
                [update],
                [global_weights],
                attack_mode,
                rng=np.random.default_rng(seed + 1000 * round_number + index),
                noisy_std_multiplier=0.25,
                poison_scale=20.0,
            )[0]
            updates.append([update])

        if mode == "secure":
            aggregated, latest_audit = defense.aggregate(
                client_ids,
                updates,
                [global_weights],
                [len(shard[0]) for shard in clients],
            )
            global_weights = aggregated[0]
            for row in latest_audit["clients"]:
                detected = row["status"] != "NORMAL"
                rejected = row["action"] == "REJECTED"
                if row["client_id"] == malicious_id:
                    true_detections += int(detected)
                    true_rejections += int(rejected)
                else:
                    false_detections += int(detected)
                    false_rejections += int(rejected)
        else:
            global_weights = fedavg(
                updates,
                [len(shard[0]) for shard in clients],
            )[0]

    result = {
        "mode": mode,
        **_evaluate(global_weights, test_x, test_y),
        "malicious_updates_submitted": rounds if mode != "clean" else 0,
        "benign_updates_submitted": rounds * (len(client_ids) - 1),
        "malicious_updates_detected": true_detections,
        "malicious_updates_rejected": true_rejections,
        "benign_updates_false_positive": false_detections,
        "benign_updates_false_rejected": false_rejections,
        "malicious_client_id": malicious_id,
        "noisy_client_id": noisy_id,
    }
    if latest_audit:
        result["aggregation_method"] = latest_audit["method"]
        result["final_client_security"] = latest_audit["clients"]
        malicious = next(row for row in latest_audit["clients"] if row["client_id"] == malicious_id)
        benign = [row["trust_score"] for row in latest_audit["clients"] if row["client_id"] != malicious_id]
        result["malicious_trust"] = malicious["trust_score"]
        result["mean_benign_trust"] = float(np.mean(benign))
        result["detection_recall"] = true_detections / rounds
        result["detection_precision"] = (
            true_detections / (true_detections + false_detections)
            if true_detections + false_detections
            else 1.0
        )
        result["benign_false_positive_rate"] = false_detections / (
            rounds * (len(client_ids) - 1)
        )
    return result


def run_experiment(rounds: int = 12, seed: int = 42) -> dict:
    clients, test_x, test_y = _make_data(seed)
    clean = _run("clean", clients, test_x, test_y, rounds, seed)
    fedavg_attack = _run("fedavg_attack", clients, test_x, test_y, rounds, seed)
    secure_attack = _run("secure", clients, test_x, test_y, rounds, seed)
    report = {
        "seed": seed,
        "rounds": rounds,
        "attack": "one noisy client plus one 20x sign-flip poisoned client",
        "clean_fedavg": clean,
        "attacked_fedavg": fedavg_attack,
        "attacked_trust_weighted": secure_attack,
        "fedavg_accuracy_degradation": clean["accuracy"] - fedavg_attack["accuracy"],
        "secure_accuracy_degradation": clean["accuracy"] - secure_attack["accuracy"],
        "secure_accuracy_gain_over_attacked_fedavg": secure_attack["accuracy"] - fedavg_attack["accuracy"],
        "secure_f1_gain_over_attacked_fedavg": secure_attack["f1"] - fedavg_attack["f1"],
    }
    return report


def run_seed_study(rounds: int = 12, seeds: List[int] | None = None) -> dict:
    seeds = seeds or [7, 42, 99]
    runs = [run_experiment(rounds=rounds, seed=seed) for seed in seeds]

    def values(path: Tuple[str, ...]) -> np.ndarray:
        extracted = []
        for run in runs:
            current = run
            for key in path:
                current = current[key]
            extracted.append(float(current))
        return np.asarray(extracted, dtype=np.float64)

    summary_paths = {
        "clean_fedavg_accuracy": ("clean_fedavg", "accuracy"),
        "clean_fedavg_f1": ("clean_fedavg", "f1"),
        "attacked_fedavg_accuracy": ("attacked_fedavg", "accuracy"),
        "secure_accuracy": ("attacked_trust_weighted", "accuracy"),
        "attacked_fedavg_f1": ("attacked_fedavg", "f1"),
        "secure_f1": ("attacked_trust_weighted", "f1"),
        "fedavg_accuracy_degradation": ("fedavg_accuracy_degradation",),
        "secure_accuracy_degradation": ("secure_accuracy_degradation",),
        "secure_accuracy_gain": ("secure_accuracy_gain_over_attacked_fedavg",),
        "secure_f1_gain": ("secure_f1_gain_over_attacked_fedavg",),
        "detection_recall": ("attacked_trust_weighted", "detection_recall"),
        "detection_precision": ("attacked_trust_weighted", "detection_precision"),
        "benign_false_positive_rate": (
            "attacked_trust_weighted", "benign_false_positive_rate"
        ),
        "malicious_trust": ("attacked_trust_weighted", "malicious_trust"),
        "mean_benign_trust": ("attacked_trust_weighted", "mean_benign_trust"),
    }
    summary = {}
    for name, path in summary_paths.items():
        metric_values = values(path)
        summary[name] = {
            "mean": float(np.mean(metric_values)),
            "std": float(np.std(metric_values)),
            "min": float(np.min(metric_values)),
            "max": float(np.max(metric_values)),
        }
    malicious_submissions = sum(
        run["attacked_trust_weighted"]["malicious_updates_submitted"] for run in runs
    )
    benign_submissions = sum(
        run["attacked_trust_weighted"]["benign_updates_submitted"] for run in runs
    )
    true_detections = sum(
        run["attacked_trust_weighted"]["malicious_updates_detected"] for run in runs
    )
    true_rejections = sum(
        run["attacked_trust_weighted"]["malicious_updates_rejected"] for run in runs
    )
    false_detections = sum(
        run["attacked_trust_weighted"]["benign_updates_false_positive"] for run in runs
    )
    false_rejections = sum(
        run["attacked_trust_weighted"]["benign_updates_false_rejected"] for run in runs
    )
    return {
        "seeds": seeds,
        "rounds_per_seed": rounds,
        "attack": "one noisy client plus one 20x sign-flip poisoned client",
        "pooled_detection": {
            "malicious_updates_submitted": malicious_submissions,
            "malicious_updates_detected": true_detections,
            "malicious_updates_rejected": true_rejections,
            "benign_updates_submitted": benign_submissions,
            "benign_updates_false_positive": false_detections,
            "benign_updates_false_rejected": false_rejections,
            "recall": true_detections / malicious_submissions,
            "precision": true_detections / (true_detections + false_detections),
            "benign_false_positive_rate": false_detections / benign_submissions,
        },
        "summary": summary,
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        help="Run a seed study, for example --seeds 7 42 99",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "results" / "byzantine_experiment.json",
    )
    parser.add_argument(
        "--promote", action="store_true",
        help="Publish only after the immutable run passes the multi-seed gate",
    )
    args = parser.parse_args()
    report = (
        run_seed_study(rounds=args.rounds, seeds=args.seeds)
        if args.seeds
        else run_experiment(rounds=args.rounds, seed=args.seed)
    )
    seeds = args.seeds if args.seeds else [args.seed]
    run = write_experiment_run(
        base=Path(__file__).parent.parent,
        experiment="byzantine_aggregation",
        protocol_version="controlled_logistic_updates_v1",
        report=report,
        seeds=seeds,
        evidence_category="controlled_simulation",
        config_paths=[Path("config/fl_config.yaml")],
        parameters={"rounds": args.rounds},
    )
    if args.promote:
        promote_run(
            base=Path(__file__).parent.parent,
            run_dir=run.run_dir,
            published_path=args.output,
            expected_experiment="byzantine_aggregation",
            expected_protocol="controlled_logistic_updates_v1",
        )
    print(json.dumps(report, indent=2))
    print(f"\nImmutable report written to {run.report_path}")


if __name__ == "__main__":
    main()
