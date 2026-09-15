"""Tests for reproducible heterogeneous FL partitions and evaluation metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fl.partitioning import (
    apply_normalized_rf_environment,
    partition_dataframe,
    resolve_non_iid_alpha,
)
from scripts.run_fl_resilience_matrix import run_configuration


CLIENTS = ("drone_1", "drone_2", "drone_3")


def _frame(groups_per_class: int = 12) -> pd.DataFrame:
    rows = []
    for attack_class in range(3):
        for group_index in range(groups_per_class):
            group = f"class-{attack_class}-group-{group_index}"
            for sequence_index in range(2):
                rows.append(
                    {
                        "sequence_group": group,
                        "sequence_index": sequence_index,
                        "attack_class": attack_class,
                        "jammed": int(attack_class != 0),
                    }
                )
    return pd.DataFrame(rows)


def test_iid_partition_is_seeded_complete_and_group_disjoint():
    dataframe = _frame()
    first = partition_dataframe(dataframe, CLIENTS, mode="iid", seed=9)
    second = partition_dataframe(dataframe, CLIENTS, mode="iid", seed=9)

    assert first.audit["group_overlap"] == 0
    assert first.audit["total_rows"] == len(dataframe)
    assert {
        client: partition["sequence_group"].tolist()
        for client, partition in first.partitions.items()
    } == {
        client: partition["sequence_group"].tolist()
        for client, partition in second.partitions.items()
    }
    group_owners = {}
    for client, partition in first.partitions.items():
        for group in partition["sequence_group"].unique():
            group_owners.setdefault(group, set()).add(client)
    assert all(len(owners) == 1 for owners in group_owners.values())


def test_dirichlet_modes_validate_alpha_and_create_reproducible_skew():
    dataframe = _frame(groups_per_class=30)
    mild = partition_dataframe(dataframe, CLIENTS, mode="mild_non_iid", seed=17)
    strong = partition_dataframe(dataframe, CLIENTS, mode="strong_non_iid", seed=17)

    assert mild.audit["non_iid_alpha"] == 2.0
    assert strong.audit["non_iid_alpha"] == 0.2
    assert all(len(partition) for partition in strong.partitions.values())
    distributions = [
        tuple(client["label_distribution"].values())
        for client in strong.audit["clients"].values()
    ]
    assert len(set(distributions)) > 1
    assert resolve_non_iid_alpha("strong_non_iid", 0.05) == 0.05


def test_rf_environment_shift_is_bounded_and_has_expected_direction():
    features = np.full((4, 5, 5), 0.5, dtype=np.float32)
    open_terrain = apply_normalized_rf_environment(features, "open_terrain")
    near_jammer = apply_normalized_rf_environment(features, "near_jammer")

    assert np.all((near_jammer >= 0.0) & (near_jammer <= 1.0))
    assert near_jammer[..., 0].mean() < open_terrain[..., 0].mean()  # RSSI
    assert near_jammer[..., 2].mean() < open_terrain[..., 2].mean()  # SINR
    assert near_jammer[..., 4].mean() > open_terrain[..., 4].mean()  # loss


def test_secure_non_iid_poison_run_reports_detection_and_drift_metrics():
    result = run_configuration(
        data_mode="strong_non_iid",
        attack="poison",
        method="TrustWeightedFedAvg",
        distillation=True,
        rounds=1,
        seed=42,
        ablation="complete_flare_secure_fl",
    )

    assert result["true_positives"] + result["false_negatives"] == 1
    assert result["true_negatives"] + result["false_positives"] == 4
    assert result["poison_updates_rejected"] == 1
    assert result["distillation_runs"] == 1
    for metric in (
        "accuracy",
        "f1",
        "mean_client_majority_distance",
        "mean_pairwise_update_cosine",
        "update_norm_dispersion",
        "client_loss_variance",
        "detection_precision",
        "detection_recall",
        "benign_client_rejection_rate",
    ):
        assert np.isfinite(result[metric])
