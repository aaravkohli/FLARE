"""Reproducible IID/non-IID, Byzantine, distillation, and ablation study.

The experiment uses FLARE's real five-feature model interface, production
Byzantine aggregation class, clipping/median/trimmed-mean primitives, and FedDF
distillation implementation. A deliberately small BiLSTM keeps the complete
matrix suitable for a laptop and CI; it is a controlled methodology experiment,
not a deployment-accuracy benchmark.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from torch.utils.data import DataLoader, TensorDataset

sys.path.insert(0, str(Path(__file__).parent.parent))

from fl.aggregator import (
    TrustWeightedAggregationStrategy,
    clip_weights_by_norm,
    coordinate_median,
    fedavg,
    trimmed_mean,
)
from fl.client import simulate_model_update_attack
from fl.data import correlated_temporal_sequence
from fl.distillation import feddf_distillation_step, generate_proxy_dataset
from fl.model import build_model, get_model_weights, set_model_weights
from fl.partitioning import (
    apply_normalized_rf_environment,
    assign_rf_environments,
    partition_dataframe,
    resolve_non_iid_alpha,
)
from fl.trust import TrustManager, UpdateAnalyzer
from provenance import promote_run, write_experiment_run


logger = logging.getLogger(__name__)
torch.set_num_threads(1)

CLIENT_IDS = tuple(f"drone_{index}" for index in range(1, 6))
DATA_MODES = ("iid", "mild_non_iid", "strong_non_iid")
ATTACK_MODES = ("none", "poison")
MATRIX_METHODS = (
    ("FedAvg", False),
    ("FedAvg", True),
    ("TrustWeightedFedAvg", False),
    ("TrustWeightedFedAvg", True),
)
MODEL_CONFIG = {
    "input_features": 5,
    "sequence_len": 4,
    "hidden_size": 6,
    "num_layers": 1,
    "dropout": 0.0,
    "num_paths": 3,
    "num_attack_classes": 5,
}

# Normalized [RSSI, PDR, SINR, latency, packet loss] centers.
_CLASS_CENTERS = np.array(
    [
        [0.76, 0.88, 0.78, 0.05, 0.05],  # none
        [0.10, 0.10, 0.12, 0.78, 0.86],  # barrage
        [0.24, 0.30, 0.30, 0.58, 0.62],  # sweep
        [0.36, 0.46, 0.42, 0.42, 0.48],  # spot
        [0.30, 0.36, 0.34, 0.52, 0.58],  # unknown communication anomaly
    ],
    dtype=np.float32,
)


@dataclass(frozen=True)
class ClientDataset:
    client_id: str
    features: torch.Tensor
    threat: torch.Tensor
    attack: torch.Tensor
    environment: str

    def __len__(self) -> int:
        return len(self.features)


def _make_sequences(
    n_samples: int,
    seed: int,
    *,
    sequence_len: int = MODEL_CONFIG["sequence_len"],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    class_probabilities = np.array([0.48, 0.16, 0.13, 0.13, 0.10])
    attack_labels = rng.choice(len(_CLASS_CENTERS), size=n_samples, p=class_probabilities)
    features = []
    for attack_class in attack_labels:
        center = np.clip(
            _CLASS_CENTERS[attack_class] + rng.normal(0.0, 0.035, size=5),
            0.0,
            1.0,
        )
        features.append(
            correlated_temporal_sequence(
                center,
                sequence_len,
                rng,
                innovation_scale=(0.02, 0.018, 0.02, 0.015, 0.018),
            )
        )
    attack_labels = attack_labels.astype(np.int64)
    threat_labels = (attack_labels != 0).astype(np.float32)
    return np.stack(features), threat_labels, attack_labels


def make_federated_data(
    mode: str,
    seed: int,
    *,
    n_train: int = 800,
    n_test: int = 400,
) -> Tuple[List[ClientDataset], Tuple[torch.Tensor, torch.Tensor, torch.Tensor], dict]:
    """Create seeded label/exposure skew and named RF feature skew."""
    train_x, train_y, train_attack = _make_sequences(n_train, seed)
    test_x, test_y, test_attack = _make_sequences(n_test, seed + 50_000)
    metadata = pd.DataFrame(
        {
            "sample_id": np.arange(n_train),
            "sequence_group": [f"sample:{index}" for index in range(n_train)],
            "sequence_index": np.zeros(n_train, dtype=int),
            "jammed": train_y.astype(int),
            "attack_class": train_attack,
        }
    )
    partitioned = partition_dataframe(
        metadata,
        CLIENT_IDS,
        mode=mode,
        non_iid_alpha=resolve_non_iid_alpha(mode),
        seed=seed + 101,
    )
    environments = assign_rf_environments(CLIENT_IDS)
    feature_strength = {"iid": 0.0, "mild_non_iid": 0.35, "strong_non_iid": 0.85}[mode]

    clients = []
    for client_id in CLIENT_IDS:
        indices = partitioned.partitions[client_id]["sample_id"].to_numpy(dtype=int)
        client_x = train_x[indices]
        environment = environments[client_id]
        if feature_strength:
            client_x = apply_normalized_rf_environment(
                client_x,
                environment,
                strength=feature_strength,
            )
        clients.append(
            ClientDataset(
                client_id=client_id,
                features=torch.tensor(client_x, dtype=torch.float32),
                threat=torch.tensor(train_y[indices], dtype=torch.float32),
                attack=torch.tensor(train_attack[indices], dtype=torch.long),
                environment=environment if feature_strength else "shared_distribution",
            )
        )
    audit = dict(partitioned.audit)
    audit["feature_skew_strength"] = feature_strength
    audit["rf_environments"] = {
        client.client_id: client.environment for client in clients
    }
    test = (
        torch.tensor(test_x, dtype=torch.float32),
        torch.tensor(test_y, dtype=torch.float32),
        torch.tensor(test_attack, dtype=torch.long),
    )
    return clients, test, audit


def _train_client(
    global_weights: List[np.ndarray],
    client: ClientDataset,
    seed: int,
) -> Tuple[List[np.ndarray], float]:
    torch.manual_seed(seed)
    model = build_model(MODEL_CONFIG)
    set_model_weights(model, global_weights)
    model.train()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.12, momentum=0.0)
    loader = DataLoader(
        TensorDataset(client.features, client.threat, client.attack),
        batch_size=min(64, len(client)),
        shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    bce = nn.BCELoss()
    ce = nn.CrossEntropyLoss()
    losses = []
    for features, threat, attack in loader:
        output = model(features)
        threat_target = threat[:, None].repeat(1, 3)
        loss = 2.0 * bce(output.path_scores, threat_target) + 0.5 * ce(
            output.attack_logits, attack
        )
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(float(loss.item()))
    return get_model_weights(model), float(np.mean(losses))


def _evaluate_model(
    weights: List[np.ndarray],
    test: Tuple[torch.Tensor, torch.Tensor, torch.Tensor],
) -> dict:
    model = build_model(MODEL_CONFIG)
    set_model_weights(model, weights)
    model.eval()
    features, threat, _ = test
    with torch.no_grad():
        predictions = (model(features).path_scores.mean(dim=1) >= 0.5).numpy().astype(int)
    labels = threat.numpy().astype(int)
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
    }


def _flatten_delta(weights: List[np.ndarray], baseline: List[np.ndarray]) -> np.ndarray:
    return np.concatenate(
        [
            (np.asarray(weight, dtype=np.float64) - np.asarray(base, dtype=np.float64)).reshape(-1)
            for weight, base in zip(weights, baseline)
        ]
    )


def _drift_metrics(
    updates: List[List[np.ndarray]],
    baseline: List[np.ndarray],
    client_losses: Sequence[float],
) -> dict:
    deltas = [_flatten_delta(update, baseline) for update in updates]
    matrix = np.stack(deltas)
    majority = np.median(matrix, axis=0)
    norms = np.linalg.norm(matrix, axis=1)
    distances = np.linalg.norm(matrix - majority, axis=1)
    pairwise_cosines = []
    for first, second in combinations(deltas, 2):
        denominator = np.linalg.norm(first) * np.linalg.norm(second)
        pairwise_cosines.append(
            float(np.dot(first, second) / denominator) if denominator > 1e-12 else 1.0
        )
    return {
        "mean_client_majority_distance": float(np.mean(distances)),
        "mean_pairwise_update_cosine": float(np.mean(pairwise_cosines)),
        "update_norm_mean": float(np.mean(norms)),
        "update_norm_dispersion": float(np.std(norms) / max(np.mean(norms), 1e-12)),
        "client_loss_variance": float(np.var(client_losses)),
    }


def _strategy_for(ablation: str | None = None) -> TrustWeightedAggregationStrategy:
    current_only = ablation == "trust_weighted_current_round"
    clip_enabled = ablation in {"trust_plus_clipping", "complete_flare_secure_fl", None}
    return TrustWeightedAggregationStrategy(
        analyzer=UpdateAnalyzer(),
        trust_manager=TrustManager(history_alpha=0.0 if current_only else 0.70),
        clip_norm=0.45 if clip_enabled else 1_000.0,
        adaptive_clip_enabled=clip_enabled,
        suspicious_fraction_for_fallback=0.5,
        fallback_strategy="coordinate_median",
    )


def _aggregate_round(
    method: str,
    updates: List[List[np.ndarray]],
    baseline: List[np.ndarray],
    counts: List[int],
    strategy: TrustWeightedAggregationStrategy | None,
) -> Tuple[List[np.ndarray], dict | None]:
    if method == "FedAvg":
        return fedavg(updates, counts), None
    if method == "fedavg_plus_clipping":
        clipped = [clip_weights_by_norm(update, baseline, 0.45) for update in updates]
        return fedavg(clipped, counts), None
    if method == "coordinate_median":
        return coordinate_median(updates), None
    if method == "trimmed_mean":
        return trimmed_mean(updates, trim_ratio=0.20), None
    if strategy is None:
        raise ValueError("trust-based aggregation requires a strategy")
    return strategy.aggregate(list(CLIENT_IDS), updates, baseline, counts)


def _distill(
    aggregated: List[np.ndarray],
    updates: List[List[np.ndarray]],
    audit: dict | None,
    seed: int,
) -> Tuple[List[np.ndarray], dict]:
    if audit is None:
        teacher_updates = updates
        teacher_weights = None
    else:
        teacher_pairs = [
            (update, client_audit)
            for update, client_audit in zip(updates, audit["clients"])
            if client_audit["action"] != "REJECTED"
        ]
        teacher_updates = [update for update, _ in teacher_pairs]
        teacher_weights = [
            max(0.0, float(client_audit.get("trust_score", 1.0)))
            for _, client_audit in teacher_pairs
        ]
    if len(teacher_updates) < 2:
        return aggregated, {"applied": False, "reason": "fewer_than_two_accepted_teachers"}
    student = build_model(MODEL_CONFIG)
    set_model_weights(student, aggregated)
    teachers = []
    for weights in teacher_updates:
        teacher = build_model(MODEL_CONFIG)
        set_model_weights(teacher, weights)
        teachers.append(teacher)
    proxy, _ = generate_proxy_dataset(
        n_samples=96,
        seq_len=MODEL_CONFIG["sequence_len"],
        n_features=MODEL_CONFIG["input_features"],
        seed=seed,
    )
    metrics = feddf_distillation_step(
        student,
        teachers,
        proxy,
        temperature=3.0,
        kd_epochs=1,
        kd_lr=0.003,
        alpha_kd=0.7,
        batch_size=48,
        device=torch.device("cpu"),
        teacher_weights=teacher_weights,
    )
    return get_model_weights(student), {"applied": True, **metrics}


def run_configuration(
    *,
    data_mode: str,
    attack: str,
    method: str,
    distillation: bool,
    rounds: int,
    seed: int,
    ablation: str | None = None,
) -> dict:
    if data_mode not in DATA_MODES or attack not in ATTACK_MODES or rounds < 1:
        raise ValueError("invalid experiment configuration")
    clients, test, partition_audit = make_federated_data(data_mode, seed)
    torch.manual_seed(seed)
    global_model = build_model(MODEL_CONFIG)
    global_weights = get_model_weights(global_model)
    strategy = _strategy_for(ablation) if method == "TrustWeightedFedAvg" else None
    malicious_id = CLIENT_IDS[-1] if attack == "poison" else None

    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    malicious_rejected = 0
    benign_rejected = 0
    convergence_round = None
    drift_history = []
    latest_audit = None
    kd_runs = []
    for round_number in range(1, rounds + 1):
        updates = []
        losses = []
        for client_index, client in enumerate(clients):
            weights, loss = _train_client(
                global_weights,
                client,
                seed + round_number * 100 + client_index,
            )
            if client.client_id == malicious_id:
                weights = simulate_model_update_attack(
                    weights,
                    global_weights,
                    "poisoned",
                    poison_scale=15.0,
                )
            updates.append(weights)
            losses.append(loss)

        drift_history.append(_drift_metrics(updates, global_weights, losses))
        global_weights, latest_audit = _aggregate_round(
            method,
            updates,
            global_weights,
            [len(client) for client in clients],
            strategy,
        )
        if latest_audit is not None:
            for row in latest_audit["clients"]:
                is_malicious = row["client_id"] == malicious_id
                detected = row["status"] != "NORMAL"
                rejected = row["action"] == "REJECTED"
                if is_malicious and detected:
                    confusion["tp"] += 1
                elif is_malicious:
                    confusion["fn"] += 1
                elif detected:
                    confusion["fp"] += 1
                else:
                    confusion["tn"] += 1
                malicious_rejected += int(is_malicious and rejected)
                benign_rejected += int(not is_malicious and rejected)
        else:
            benign_count = len(CLIENT_IDS) - int(malicious_id is not None)
            confusion["tn"] += benign_count
            confusion["fn"] += int(malicious_id is not None)

        if distillation:
            global_weights, kd_metrics = _distill(
                global_weights,
                updates,
                latest_audit,
                seed + 10_000 + round_number,
            )
            kd_runs.append(kd_metrics)
        round_metrics = _evaluate_model(global_weights, test)
        if convergence_round is None and round_metrics["f1"] >= 0.80:
            convergence_round = round_number

    prediction_metrics = _evaluate_model(global_weights, test)
    tp, fp, tn, fn = (confusion[key] for key in ("tp", "fp", "tn", "fn"))
    detection_precision = tp / (tp + fp) if tp + fp else 0.0
    detection_recall = tp / (tp + fn) if tp + fn else 0.0
    detection_f1 = (
        2.0 * detection_precision * detection_recall / (detection_precision + detection_recall)
        if detection_precision + detection_recall
        else 0.0
    )
    mean_drift = {
        key: float(np.mean([row[key] for row in drift_history]))
        for key in drift_history[0]
    }
    benign_updates = rounds * (len(CLIENT_IDS) - int(malicious_id is not None))
    malicious_trust = None
    benign_trust = None
    aggregation_used = method
    if latest_audit is not None:
        aggregation_used = latest_audit["method"]
        benign_values = [
            row["trust_score"]
            for row in latest_audit["clients"]
            if row["client_id"] != malicious_id
        ]
        benign_trust = float(np.mean(benign_values))
        if malicious_id is not None:
            malicious_trust = next(
                row["trust_score"]
                for row in latest_audit["clients"]
                if row["client_id"] == malicious_id
            )

    return {
        "seed": seed,
        "data_mode": data_mode,
        "non_iid_alpha": partition_audit["non_iid_alpha"],
        "feature_skew_strength": partition_audit["feature_skew_strength"],
        "attack": attack,
        "aggregation": method,
        "aggregation_used_final_round": aggregation_used,
        "distillation": distillation,
        "ablation": ablation,
        "training_rounds": rounds,
        "convergence_round_f1_0_80": convergence_round,
        **prediction_metrics,
        **mean_drift,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "detection_precision": detection_precision,
        "detection_recall": detection_recall,
        "detection_f1": detection_f1,
        "malicious_client_detection_rate": detection_recall,
        "poison_updates_rejected": malicious_rejected,
        "benign_updates_rejected": benign_rejected,
        "benign_client_rejection_rate": benign_rejected / max(benign_updates, 1),
        "average_malicious_trust": malicious_trust,
        "average_benign_trust": benign_trust,
        "distillation_runs": sum("kd_loss" in metrics for metrics in kd_runs),
        "distillation_applied_runs": sum(bool(metrics.get("applied")) for metrics in kd_runs),
        "distillation_rollbacks": sum(bool(metrics.get("rolled_back")) for metrics in kd_runs),
        "mean_distillation_duration_ms": (
            float(np.mean([metrics.get("duration_ms", 0.0) for metrics in kd_runs]))
            if kd_runs else 0.0
        ),
        "partition_audit": partition_audit,
    }


def _summarize(runs: Sequence[dict], keys: Sequence[str]) -> List[dict]:
    groups: Dict[tuple, List[dict]] = {}
    for run in runs:
        group_key = tuple(run[key] for key in keys)
        groups.setdefault(group_key, []).append(run)
    metric_names = (
        "accuracy",
        "precision",
        "recall",
        "f1",
        "mean_client_majority_distance",
        "mean_pairwise_update_cosine",
        "update_norm_dispersion",
        "client_loss_variance",
        "detection_precision",
        "detection_recall",
        "detection_f1",
        "benign_client_rejection_rate",
        "poison_updates_rejected",
        "benign_updates_rejected",
    )
    summaries = []
    for group_key, group_runs in groups.items():
        row = dict(zip(keys, group_key))
        row["seeds"] = [run["seed"] for run in group_runs]
        for metric in metric_names:
            values = np.asarray([run[metric] for run in group_runs], dtype=np.float64)
            row[f"{metric}_mean"] = float(np.mean(values))
            row[f"{metric}_std"] = float(np.std(values))
        for trust_metric in ("average_malicious_trust", "average_benign_trust"):
            values = [run[trust_metric] for run in group_runs if run[trust_metric] is not None]
            row[f"{trust_metric}_mean"] = float(np.mean(values)) if values else None
        summaries.append(row)
    return summaries


ABLATIONS = (
    ("fedavg", "FedAvg", False),
    ("fedavg_plus_clipping", "fedavg_plus_clipping", False),
    ("coordinate_median", "coordinate_median", False),
    ("trimmed_mean", "trimmed_mean", False),
    ("trust_weighted_current_round", "TrustWeightedFedAvg", False),
    ("trust_plus_historical_reputation", "TrustWeightedFedAvg", False),
    ("trust_plus_clipping", "TrustWeightedFedAvg", False),
    ("trust_plus_distillation", "TrustWeightedFedAvg", True),
    ("complete_flare_secure_fl", "TrustWeightedFedAvg", True),
)


def run_study(rounds: int = 4, seeds: Sequence[int] = (7, 42, 99)) -> dict:
    matrix_runs = []
    for seed in seeds:
        for data_mode in DATA_MODES:
            for attack in ATTACK_MODES:
                for method, distillation in MATRIX_METHODS:
                    matrix_runs.append(
                        run_configuration(
                            data_mode=data_mode,
                            attack=attack,
                            method=method,
                            distillation=distillation,
                            rounds=rounds,
                            seed=int(seed),
                        )
                    )
    ablation_runs = []
    for seed in seeds:
        for name, method, distillation in ABLATIONS:
            ablation_runs.append(
                run_configuration(
                    data_mode="strong_non_iid",
                    attack="poison",
                    method=method,
                    distillation=distillation,
                    rounds=rounds,
                    seed=int(seed),
                    ablation=name,
                )
            )
    return {
        "study": "FLARE IID/non-IID Byzantine resilience and FedDF ablation",
        "scope": "controlled synthetic five-feature BiLSTM methodology experiment",
        "seeds": [int(seed) for seed in seeds],
        "rounds": rounds,
        "model_config": MODEL_CONFIG,
        "matrix_summary": _summarize(
            matrix_runs,
            ("data_mode", "attack", "aggregation", "distillation"),
        ),
        "ablation_summary": _summarize(ablation_runs, ("ablation",)),
        "matrix_runs": matrix_runs,
        "ablation_runs": ablation_runs,
    }


def _csv_rows(report: dict) -> List[dict]:
    rows = []
    for study_name, runs_key in (("matrix", "matrix_runs"), ("ablation", "ablation_runs")):
        for run in report[runs_key]:
            row = {key: value for key, value in run.items() if key != "partition_audit"}
            row["study"] = study_name
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 99])
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path(__file__).parent.parent / "results" / "fl_resilience_matrix.json",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path(__file__).parent.parent / "results" / "fl_resilience_matrix.csv",
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    report = run_study(rounds=args.rounds, seeds=args.seeds)
    rows = _csv_rows(report)
    fieldnames = sorted({key for row in rows for key in row})
    from io import StringIO
    csv_buffer = StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    base = Path(__file__).parent.parent
    run_artifact = write_experiment_run(
        base=base,
        experiment="fl_resilience_matrix",
        protocol_version="controlled_bilstm_resilience_v1",
        report=report,
        seeds=args.seeds,
        evidence_category="controlled_simulation",
        config_paths=[Path("config/fl_config.yaml")],
        parameters={"rounds": args.rounds},
        extra_files={"report.csv": csv_buffer.getvalue()},
    )
    if args.promote:
        promote_run(
            base=base,
            run_dir=run_artifact.run_dir,
            published_path=args.json_output,
            expected_experiment="fl_resilience_matrix",
            expected_protocol="controlled_bilstm_resilience_v1",
            extra_publications={"report.csv": args.csv_output},
        )
    print(json.dumps({
        "matrix_scenarios": len(report["matrix_runs"]),
        "ablation_runs": len(report["ablation_runs"]),
        "json": str(run_artifact.report_path),
        "csv": str(run_artifact.run_dir / "report.csv"),
    }, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    main()
