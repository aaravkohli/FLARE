"""Shared temporal-window and leakage-resistant split utilities for FL data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit


FEATURE_COLS = ("rssi", "pdr", "sinr", "latency", "packet_loss")
FEATURE_MINS = np.array([-120.0, 0.0, -10.0, 0.0, 0.0], dtype=np.float32)
FEATURE_MAXS = np.array([-20.0, 1.0, 30.0, 1000.0, 1.0], dtype=np.float32)
DEFAULT_GROUP_COLUMN = "sequence_group"
DEFAULT_ORDER_COLUMN = "sequence_index"
TEMPORAL_DATA_DEFINITION = "grouped_temporal_v1"


@dataclass(frozen=True)
class TemporalWindows:
    """Window arrays plus provenance needed to audit ordering and leakage."""

    features: np.ndarray
    threat_labels: np.ndarray
    attack_labels: np.ndarray
    group_ids: tuple[str, ...]
    end_order: tuple[object, ...]

    def __len__(self) -> int:
        return len(self.features)


def normalise_feature_array(features: np.ndarray) -> np.ndarray:
    """Normalize raw physical features while preserving pre-normalized CSVs."""
    values = np.asarray(features, dtype=np.float32).copy()
    if values.ndim != 2 or values.shape[1] != len(FEATURE_COLS):
        raise ValueError(
            f"features must have shape [rows, {len(FEATURE_COLS)}]"
        )
    if not np.isfinite(values).all():
        raise ValueError("features must contain only finite values")

    for feature_index in range(values.shape[1]):
        column = values[:, feature_index]
        if column.min() < -0.01 or column.max() > 1.01:
            column = (
                (column - FEATURE_MINS[feature_index])
                / (FEATURE_MAXS[feature_index] - FEATURE_MINS[feature_index])
            )
        values[:, feature_index] = np.clip(column, 0.0, 1.0)
    return values


def correlated_temporal_sequence(
    base_features: Sequence[float],
    sequence_len: int,
    rng: np.random.Generator,
    *,
    innovation_scale: Sequence[float] = (0.018, 0.015, 0.018, 0.012, 0.015),
) -> np.ndarray:
    """Create a bounded correlated random walk around normalized RF features."""
    if sequence_len < 1:
        raise ValueError("sequence_len must be at least 1")
    base = np.asarray(base_features, dtype=np.float32)
    scale = np.asarray(innovation_scale, dtype=np.float32)
    if base.shape != (len(FEATURE_COLS),) or scale.shape != base.shape:
        raise ValueError(f"base_features and innovation_scale must have length {len(FEATURE_COLS)}")
    if not np.isfinite(base).all() or not np.isfinite(scale).all():
        raise ValueError("temporal sequence inputs must be finite")
    if (scale < 0).any():
        raise ValueError("innovation_scale must be non-negative")

    innovations = rng.normal(0.0, scale, size=(sequence_len, len(base))).astype(
        np.float32
    )
    innovations[0] = 0.0
    drift = np.cumsum(innovations, axis=0)
    return np.clip(base + drift, 0.0, 1.0).astype(np.float32)


def _window_group_columns(df: pd.DataFrame) -> list[str]:
    if DEFAULT_GROUP_COLUMN in df.columns:
        columns = [DEFAULT_GROUP_COLUMN]
    else:
        columns = [column for column in ("source",) if column in df.columns]
    columns.extend(
        column for column in ("drone_id", "path")
        if column in df.columns and column not in columns
    )
    return columns


def build_temporal_windows(
    df: pd.DataFrame,
    sequence_len: int,
    *,
    stride: int = 1,
) -> TemporalWindows:
    """Build ordered sliding windows without crossing source/drone/path groups.

    The target is taken from the final row in each window, so no future label is
    used to score earlier observations. When preprocessing metadata is absent,
    the original CSV order is retained within the available source/drone/path
    grouping for backward compatibility.
    """
    if sequence_len < 1:
        raise ValueError("sequence_len must be at least 1")
    if stride < 1:
        raise ValueError("stride must be at least 1")

    required = [*FEATURE_COLS, "jammed"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"dataset is missing required columns: {missing}")

    clean = df.dropna(subset=required).copy()
    clean["_input_order"] = np.arange(len(clean), dtype=np.int64)
    attack_column = "attack_class" if "attack_class" in clean.columns else None
    group_columns = _window_group_columns(clean)

    if group_columns:
        grouped = clean.groupby(group_columns, sort=False, dropna=False)
    else:
        grouped = [("all", clean)]

    feature_windows: list[np.ndarray] = []
    threat_labels: list[np.ndarray] = []
    attack_labels: list[int] = []
    group_ids: list[str] = []
    end_order: list[object] = []

    for raw_group_id, group in grouped:
        order_column = (
            DEFAULT_ORDER_COLUMN
            if DEFAULT_ORDER_COLUMN in group.columns
            else "_input_order"
        )
        ordered = group.sort_values(order_column, kind="stable")
        if len(ordered) < sequence_len:
            continue

        features = normalise_feature_array(
            ordered.loc[:, FEATURE_COLS].to_numpy(dtype=np.float32)
        )
        jammed = ordered["jammed"].to_numpy(dtype=np.int8)
        if attack_column is not None:
            attacks = ordered[attack_column].to_numpy(dtype=np.int64)
        else:
            attacks = jammed.astype(np.int64)
        attacks = np.clip(attacks, 0, 4)
        orders = ordered[order_column].to_numpy()
        group_id = raw_group_id if isinstance(raw_group_id, tuple) else (raw_group_id,)
        group_key = "|".join(str(part) for part in group_id)

        for start in range(0, len(ordered) - sequence_len + 1, stride):
            end = start + sequence_len
            label = float(jammed[end - 1])
            feature_windows.append(features[start:end])
            threat_labels.append(np.full(3, label, dtype=np.float32))
            attack_labels.append(int(attacks[end - 1]))
            group_ids.append(group_key)
            end_order.append(orders[end - 1])

    if not feature_windows:
        empty_features = np.empty(
            (0, sequence_len, len(FEATURE_COLS)), dtype=np.float32
        )
        return TemporalWindows(
            features=empty_features,
            threat_labels=np.empty((0, 3), dtype=np.float32),
            attack_labels=np.empty((0,), dtype=np.int64),
            group_ids=(),
            end_order=(),
        )

    return TemporalWindows(
        features=np.stack(feature_windows).astype(np.float32),
        threat_labels=np.stack(threat_labels).astype(np.float32),
        attack_labels=np.asarray(attack_labels, dtype=np.int64),
        group_ids=tuple(group_ids),
        end_order=tuple(end_order),
    )


def split_by_sequence_group(
    df: pd.DataFrame,
    *,
    test_fraction: float = 0.2,
    random_state: int = 42,
    group_column: str = DEFAULT_GROUP_COLUMN,
    label_column: str = "jammed",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select a deterministic, group-disjoint split with balanced labels."""
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    for column in (group_column, label_column):
        if column not in df.columns:
            raise ValueError(f"dataset is missing split column {column!r}")
    if df[group_column].nunique(dropna=False) < 2:
        raise ValueError("at least two sequence groups are required for a split")

    labels = df[label_column].astype(int).to_numpy()
    groups = df[group_column].astype(str).to_numpy()
    splitter = GroupShuffleSplit(
        n_splits=min(64, max(8, len(np.unique(groups)))),
        test_size=test_fraction,
        random_state=random_state,
    )
    overall_positive_rate = float(labels.mean())
    candidates: list[tuple[float, np.ndarray, np.ndarray]] = []
    for train_indices, test_indices in splitter.split(df, labels, groups):
        train_labels = labels[train_indices]
        test_labels = labels[test_indices]
        missing_class_penalty = 0.0
        if len(np.unique(labels)) > 1:
            missing_class_penalty = 10.0 * (
                int(len(np.unique(train_labels)) < 2)
                + int(len(np.unique(test_labels)) < 2)
            )
        score = (
            abs(len(test_indices) / len(df) - test_fraction)
            + abs(float(train_labels.mean()) - overall_positive_rate)
            + abs(float(test_labels.mean()) - overall_positive_rate)
            + missing_class_penalty
        )
        candidates.append((score, train_indices, test_indices))

    _, train_indices, test_indices = min(candidates, key=lambda candidate: candidate[0])
    train = df.iloc[train_indices].copy()
    test = df.iloc[test_indices].copy()
    overlap = set(train[group_column].astype(str)) & set(test[group_column].astype(str))
    if overlap:
        raise RuntimeError("group-aware split produced overlapping sequence groups")
    return train, test
