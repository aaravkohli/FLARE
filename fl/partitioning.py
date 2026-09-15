"""Reproducible IID and non-IID partitioning for FLARE client datasets.

Partitions are assigned at the temporal-group level so overlapping windows from
one capture are never split across drones. Label/attack-exposure skew uses a
standard per-class Dirichlet allocation. Optional RF-environment transforms are
simulation-only and operate on the canonical normalized five-feature tensor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Sequence

import numpy as np
import pandas as pd

from fl.data import DEFAULT_GROUP_COLUMN, DEFAULT_ORDER_COLUMN, FEATURE_COLS


PARTITION_MODES = ("iid", "mild_non_iid", "strong_non_iid")
DEFAULT_NON_IID_ALPHA = {
    "iid": None,
    "mild_non_iid": 2.0,
    "strong_non_iid": 0.2,
}
RF_ENVIRONMENTS = ("open_terrain", "urban_interference", "near_jammer", "far_from_jammer")

# Deltas in the normalized [RSSI, PDR, SINR, latency, packet-loss] feature space.
_ENVIRONMENT_DELTAS = {
    "open_terrain": np.array([0.07, 0.05, 0.07, -0.03, -0.04], dtype=np.float32),
    "urban_interference": np.array([-0.05, -0.05, -0.06, 0.06, 0.07], dtype=np.float32),
    "near_jammer": np.array([-0.18, -0.22, -0.20, 0.20, 0.24], dtype=np.float32),
    "far_from_jammer": np.array([0.03, 0.02, 0.04, -0.01, -0.02], dtype=np.float32),
}


@dataclass(frozen=True)
class PartitionResult:
    """Client dataframes plus machine-readable heterogeneity evidence."""

    partitions: Dict[str, pd.DataFrame]
    audit: dict


def resolve_non_iid_alpha(mode: str, alpha: float | None = None) -> float | None:
    """Resolve and validate the Dirichlet concentration for a partition mode."""
    if mode not in PARTITION_MODES:
        raise ValueError(f"partition mode must be one of {PARTITION_MODES}")
    if mode == "iid":
        return None
    value = DEFAULT_NON_IID_ALPHA[mode] if alpha is None else float(alpha)
    if not np.isfinite(value) or value <= 0.0:
        raise ValueError("non_iid_alpha must be finite and positive")
    return value


def _representative_group_labels(
    dataframe: pd.DataFrame,
    group_column: str,
    label_column: str,
) -> pd.Series:
    """Choose a deterministic majority label for every temporal group."""
    return dataframe.groupby(group_column, sort=False, dropna=False)[label_column].agg(
        lambda values: values.value_counts(sort=False).sort_index().idxmax()
    )


def _rebalance_empty_clients(assignments: Dict[str, list[str]]) -> None:
    """Move whole groups from the largest partition until every client is non-empty."""
    empty = [client_id for client_id, groups in assignments.items() if not groups]
    for empty_client in empty:
        donor = max(assignments, key=lambda client_id: len(assignments[client_id]))
        if len(assignments[donor]) <= 1:
            raise ValueError("not enough temporal groups to give every client data")
        assignments[empty_client].append(assignments[donor].pop())


def partition_dataframe(
    dataframe: pd.DataFrame,
    client_ids: Sequence[str],
    *,
    mode: str = "iid",
    non_iid_alpha: float | None = None,
    seed: int = 42,
    group_column: str = DEFAULT_GROUP_COLUMN,
    label_column: str = "attack_class",
) -> PartitionResult:
    """Partition a training dataframe without splitting temporal groups.

    ``iid`` shuffles groups and assigns them round-robin. Non-IID modes draw a
    client allocation independently for each label using a Dirichlet
    distribution; smaller alpha values produce stronger label/attack-exposure
    skew. The original dataframe is not modified.
    """
    clients = tuple(str(client_id) for client_id in client_ids)
    if not clients or len(set(clients)) != len(clients):
        raise ValueError("client_ids must be non-empty and unique")
    for column in (group_column, label_column):
        if column not in dataframe.columns:
            raise ValueError(f"dataset is missing partition column {column!r}")
    if dataframe.empty:
        raise ValueError("cannot partition an empty dataframe")

    alpha = resolve_non_iid_alpha(mode, non_iid_alpha)
    group_labels = _representative_group_labels(dataframe, group_column, label_column)
    if len(group_labels) < len(clients):
        raise ValueError("fewer temporal groups than clients")

    rng = np.random.default_rng(seed)
    assignments: Dict[str, list[str]] = {client_id: [] for client_id in clients}
    if mode == "iid":
        groups = group_labels.index.astype(str).to_numpy(copy=True)
        rng.shuffle(groups)
        for index, group_id in enumerate(groups):
            assignments[clients[index % len(clients)]].append(group_id)
    else:
        for label in sorted(group_labels.unique(), key=str):
            groups = group_labels[group_labels == label].index.astype(str).to_numpy(copy=True)
            rng.shuffle(groups)
            proportions = rng.dirichlet(np.full(len(clients), alpha, dtype=np.float64))
            counts = rng.multinomial(len(groups), proportions)
            cursor = 0
            for client_id, count in zip(clients, counts):
                assignments[client_id].extend(groups[cursor : cursor + count].tolist())
                cursor += count
        _rebalance_empty_clients(assignments)

    group_values = dataframe[group_column].astype(str)
    partitions: Dict[str, pd.DataFrame] = {}
    for client_id in clients:
        partition = dataframe[group_values.isin(assignments[client_id])].copy()
        sort_columns = [
            column for column in (group_column, DEFAULT_ORDER_COLUMN) if column in partition.columns
        ]
        if sort_columns:
            partition = partition.sort_values(sort_columns, kind="stable")
        partition["drone_id"] = client_id
        partitions[client_id] = partition.reset_index(drop=True)

    all_assigned = [group_id for groups in assignments.values() for group_id in groups]
    if len(all_assigned) != len(set(all_assigned)) or set(all_assigned) != set(
        group_labels.index.astype(str)
    ):
        raise RuntimeError("temporal groups were lost or assigned to multiple clients")

    audit_clients = {}
    for client_id, partition in partitions.items():
        label_counts = partition[label_column].value_counts().sort_index()
        audit_clients[client_id] = {
            "rows": int(len(partition)),
            "groups": int(partition[group_column].nunique(dropna=False)),
            "positive_rate": float(partition["jammed"].mean()) if "jammed" in partition else None,
            "label_distribution": {
                str(label): int(count) for label, count in label_counts.items()
            },
        }
    return PartitionResult(
        partitions=partitions,
        audit={
            "mode": mode,
            "non_iid_alpha": alpha,
            "seed": int(seed),
            "group_column": group_column,
            "label_column": label_column,
            "total_rows": int(sum(len(partition) for partition in partitions.values())),
            "total_groups": int(len(group_labels)),
            "group_overlap": 0,
            "clients": audit_clients,
        },
    )


def apply_normalized_rf_environment(
    features: np.ndarray,
    environment: str,
    *,
    strength: float = 1.0,
) -> np.ndarray:
    """Apply a reproducible simulation-only RF feature-distribution shift."""
    if environment not in _ENVIRONMENT_DELTAS:
        raise ValueError(f"environment must be one of {RF_ENVIRONMENTS}")
    strength = float(strength)
    if not np.isfinite(strength) or strength < 0.0:
        raise ValueError("feature skew strength must be finite and non-negative")
    values = np.asarray(features, dtype=np.float32)
    if values.shape[-1] != len(FEATURE_COLS) or not np.isfinite(values).all():
        raise ValueError(f"features must be finite with final dimension {len(FEATURE_COLS)}")
    return np.clip(values + strength * _ENVIRONMENT_DELTAS[environment], 0.0, 1.0).astype(
        np.float32
    )


def assign_rf_environments(client_ids: Sequence[str]) -> Mapping[str, str]:
    """Assign stable, named RF environments to simulated clients."""
    return {
        str(client_id): RF_ENVIRONMENTS[index % len(RF_ENVIRONMENTS)]
        for index, client_id in enumerate(client_ids)
    }
