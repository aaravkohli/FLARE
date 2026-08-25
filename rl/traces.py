"""Versioned, synchronized three-path traces for routing replay and evaluation.

Each row is one decision timestep and contains the complete direct, satellite,
and mesh state observed at that instant.  This avoids manufacturing three route
states from an unrelated single-link dataset row.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from rl.reward import PATH_NAMES


TRACE_VERSION = "synchronized_three_path_v1"
TRACE_SOURCE = "generated:rl.traces"
ROBUSTNESS_SCENARIOS = (
    "iid",
    "persistent_spot",
    "barrage",
    "smart_jammer",
)
SUPPORTED_SCENARIOS = ("clean", *ROBUSTNESS_SCENARIOS, "reactive")
TRACE_METADATA_COLUMNS = (
    "trace_version",
    "source",
    "scenario",
    "generation_seed",
    "episode_id",
    "step",
)
PATH_TRACE_FIELDS = (
    "threat_score",
    "latency_norm",
    "packet_loss",
    "jammed",
)
TRACE_COLUMNS = TRACE_METADATA_COLUMNS + tuple(
    f"{path_name}_{field}"
    for path_name in PATH_NAMES
    for field in PATH_TRACE_FIELDS
)


class TraceValidationError(ValueError):
    """Raised when a replay trace violates the synchronized-path contract."""


def _sample_path_state(
    rng: np.random.Generator,
    *,
    jammed: bool,
    conceal_threat: bool = False,
) -> tuple[float, float, float, int]:
    if jammed:
        threat = float(
            rng.uniform(0.05, 0.25) if conceal_threat else rng.uniform(0.6, 1.0)
        )
        if conceal_threat:
            latency = float(rng.uniform(0.75, 1.0))
            packet_loss = float(rng.uniform(0.75, 1.0))
        else:
            latency = float(0.1 + 0.9 * threat)
            packet_loss = float(np.clip(threat * 0.8 + rng.uniform(0.0, 0.1), 0.0, 1.0))
    else:
        threat = float(rng.uniform(0.0, 0.3))
        latency = float(0.1 + 0.9 * threat)
        packet_loss = float(np.clip(threat * 0.8 + rng.uniform(0.0, 0.1), 0.0, 1.0))
    return threat, latency, packet_loss, int(jammed)


def _jam_state_for_step(
    scenario: str,
    *,
    episode_index: int,
    step: int,
    episode_phase: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Return jammed and threat-concealment masks for one synchronized step."""
    jammed = np.zeros(len(PATH_NAMES), dtype=bool)
    concealment = np.zeros(len(PATH_NAMES), dtype=bool)

    if scenario == "clean":
        pass
    elif scenario == "iid":
        jammed = rng.random(len(PATH_NAMES)) < 0.2
    elif scenario == "persistent_spot":
        cycle_position = (step + episode_phase) % 80
        if cycle_position < 50:
            cycle = (step + episode_phase) // 80
            jammed[(episode_index + cycle) % len(PATH_NAMES)] = True
    elif scenario == "barrage":
        if (step + episode_phase) % 100 < 55:
            jammed[:] = True
    elif scenario == "reactive":
        if (step + episode_phase) % 2 == 1:
            jammed[(episode_index + step) % len(PATH_NAMES)] = True
    elif scenario == "smart_jammer":
        cycle_position = (step + episode_phase) % 90
        if cycle_position < 60:
            cycle = (step + episode_phase) // 90
            target = (episode_index + cycle) % len(PATH_NAMES)
            jammed[target] = True
            concealment[target] = True
    else:  # pragma: no cover - guarded by the public generator
        raise ValueError(f"unsupported trace scenario {scenario!r}")
    return jammed, concealment


def generate_synchronized_trace(
    *,
    scenario: str,
    n_episodes: int,
    steps_per_episode: int,
    base_seed: int,
) -> pd.DataFrame:
    """Generate a deterministic, action-independent three-path trace frame.

    ``iid`` matches the current ``DronePathEnv`` attack probability and feature
    formulas.  The other scenarios intentionally introduce persistence,
    simultaneous wideband disruption, or a low-threat-score smart jammer for
    out-of-distribution stress testing.
    """
    if scenario not in SUPPORTED_SCENARIOS:
        raise ValueError(
            f"scenario must be one of {SUPPORTED_SCENARIOS}, got {scenario!r}"
        )
    if n_episodes < 1:
        raise ValueError("n_episodes must be at least 1")
    if steps_per_episode < 1:
        raise ValueError("steps_per_episode must be at least 1")

    rows: list[dict] = []
    for episode_index in range(n_episodes):
        episode_seed = int(base_seed) + episode_index
        rng = np.random.default_rng(episode_seed)
        episode_phase = int(rng.integers(0, 300))
        episode_id = f"{scenario}:seed={int(base_seed)}:{episode_index:05d}"

        for step in range(steps_per_episode):
            jammed, concealment = _jam_state_for_step(
                scenario,
                episode_index=episode_index,
                step=step,
                episode_phase=episode_phase,
                rng=rng,
            )
            row: dict[str, object] = {
                "trace_version": TRACE_VERSION,
                "source": TRACE_SOURCE,
                "scenario": scenario,
                "generation_seed": int(base_seed),
                "episode_id": episode_id,
                "step": step,
            }
            for path_index, path_name in enumerate(PATH_NAMES):
                threat, latency, packet_loss, jammed_value = _sample_path_state(
                    rng,
                    jammed=bool(jammed[path_index]),
                    conceal_threat=bool(concealment[path_index]),
                )
                row[f"{path_name}_threat_score"] = round(threat, 6)
                row[f"{path_name}_latency_norm"] = round(latency, 6)
                row[f"{path_name}_packet_loss"] = round(packet_loss, 6)
                row[f"{path_name}_jammed"] = jammed_value
            rows.append(row)

    frame = pd.DataFrame(rows, columns=TRACE_COLUMNS)
    return validate_synchronized_trace(frame)


def generate_scenario_mixture_trace(
    *,
    episode_counts: Mapping[str, int],
    steps_per_episode: int,
    base_seed: int,
    scenario_seed_stride: int = 100_000,
) -> pd.DataFrame:
    """Generate one deterministic trace with a documented scenario mixture.

    Scenarios are emitted in the canonical ``SUPPORTED_SCENARIOS`` order and
    receive non-overlapping generation-seed ranges.  ``smart_jammer`` can be
    omitted from training and retained as an unseen stress test.
    """
    if not episode_counts:
        raise ValueError("episode_counts must contain at least one scenario")
    unknown = set(episode_counts) - set(SUPPORTED_SCENARIOS)
    if unknown:
        raise ValueError(f"unsupported scenarios: {sorted(unknown)}")
    if steps_per_episode < 1:
        raise ValueError("steps_per_episode must be at least 1")
    if scenario_seed_stride < 1:
        raise ValueError("scenario_seed_stride must be at least 1")

    normalized_counts: dict[str, int] = {}
    for scenario, count in episode_counts.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(
                f"episode count for {scenario!r} must be a positive integer"
            )
        if count > scenario_seed_stride:
            raise ValueError(
                f"episode count for {scenario!r} exceeds scenario_seed_stride; "
                "generation-seed ranges would overlap"
            )
        normalized_counts[scenario] = count

    frames = []
    for scenario_index, scenario in enumerate(SUPPORTED_SCENARIOS):
        count = normalized_counts.get(scenario)
        if count is None:
            continue
        frames.append(
            generate_synchronized_trace(
                scenario=scenario,
                n_episodes=count,
                steps_per_episode=steps_per_episode,
                base_seed=int(base_seed) + scenario_index * scenario_seed_stride,
            )
        )
    return validate_synchronized_trace(pd.concat(frames, ignore_index=True))


def _nonempty_strings(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        values = frame[column]
        if values.isna().any() or values.astype(str).str.strip().eq("").any():
            raise TraceValidationError(f"{column} must contain non-empty strings")


def validate_synchronized_trace(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and return a canonical copy of a synchronized trace frame."""
    if frame.empty:
        raise TraceValidationError("trace must contain at least one row")
    missing = [column for column in TRACE_COLUMNS if column not in frame.columns]
    if missing:
        raise TraceValidationError(f"trace is missing required columns: {missing}")

    validated = frame.loc[:, TRACE_COLUMNS].copy()
    _nonempty_strings(validated, ("source", "scenario", "episode_id"))
    versions = set(validated["trace_version"].astype(str))
    if versions != {TRACE_VERSION}:
        raise TraceValidationError(
            f"trace_version must be exactly {TRACE_VERSION!r}, got {sorted(versions)}"
        )
    unsupported = set(validated["scenario"].astype(str)) - set(SUPPORTED_SCENARIOS)
    if unsupported:
        raise TraceValidationError(f"unsupported scenarios: {sorted(unsupported)}")

    generation_seeds = pd.to_numeric(validated["generation_seed"], errors="coerce")
    if generation_seeds.isna().any() or not np.isfinite(generation_seeds.to_numpy()).all():
        raise TraceValidationError("generation_seed must contain finite integers")
    if not np.equal(generation_seeds, np.floor(generation_seeds)).all():
        raise TraceValidationError("generation_seed must contain integers")
    validated["generation_seed"] = generation_seeds.astype(np.int64)

    step_values = pd.to_numeric(validated["step"], errors="coerce")
    if step_values.isna().any() or not np.isfinite(step_values.to_numpy()).all():
        raise TraceValidationError("step must contain finite integers")
    if (step_values < 0).any() or not np.equal(step_values, np.floor(step_values)).all():
        raise TraceValidationError("step must contain non-negative integers")
    validated["step"] = step_values.astype(np.int64)

    numeric_feature_columns = [
        f"{path_name}_{field}"
        for path_name in PATH_NAMES
        for field in ("threat_score", "latency_norm", "packet_loss")
    ]
    for column in numeric_feature_columns:
        values = pd.to_numeric(validated[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy()).all():
            raise TraceValidationError(f"{column} must contain finite numbers")
        if ((values < 0.0) | (values > 1.0)).any():
            raise TraceValidationError(f"{column} must be normalized to [0, 1]")
        validated[column] = values.astype(np.float32)

    for path_name in PATH_NAMES:
        column = f"{path_name}_jammed"
        values = pd.to_numeric(validated[column], errors="coerce")
        if values.isna().any() or not values.isin([0, 1]).all():
            raise TraceValidationError(f"{column} must contain only 0 or 1")
        validated[column] = values.astype(np.int8)

    duplicate_steps = validated.duplicated(["episode_id", "step"])
    if duplicate_steps.any():
        duplicate = validated.loc[duplicate_steps, ["episode_id", "step"]].iloc[0]
        raise TraceValidationError(
            f"duplicate timestep for episode {duplicate['episode_id']!r}: "
            f"step {int(duplicate['step'])}"
        )

    for episode_id, episode in validated.groupby("episode_id", sort=False):
        expected_steps = list(range(len(episode)))
        actual_steps = sorted(episode["step"].astype(int).tolist())
        if actual_steps != expected_steps:
            raise TraceValidationError(
                f"episode {episode_id!r} steps must be contiguous from zero"
            )
        for column in ("trace_version", "source", "scenario", "generation_seed"):
            if episode[column].nunique(dropna=False) != 1:
                raise TraceValidationError(
                    f"episode {episode_id!r} has multiple {column} values"
                )

    return validated.sort_values(["episode_id", "step"], kind="stable").reset_index(drop=True)


def load_synchronized_trace(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"synchronized RL trace not found: {path}\n"
            "Generate it with: python -m rl.traces --scenario iid"
        )
    return validate_synchronized_trace(pd.read_csv(path))


def validate_disjoint_trace_files(
    training_csv: str | Path,
    evaluation_csv: str | Path,
) -> dict:
    """Validate two trace files and prove their episode identities are disjoint."""
    training = load_synchronized_trace(training_csv)
    evaluation = load_synchronized_trace(evaluation_csv)
    training_ids = set(training["episode_id"].astype(str))
    evaluation_ids = set(evaluation["episode_id"].astype(str))
    overlap = training_ids & evaluation_ids
    if overlap:
        examples = sorted(overlap)[:5]
        raise TraceValidationError(
            "training and evaluation traces share episode IDs: "
            f"{examples}"
        )
    training_fingerprint = trace_fingerprint(training)
    evaluation_fingerprint = trace_fingerprint(evaluation)
    if training_fingerprint == evaluation_fingerprint:
        raise TraceValidationError(
            "training and evaluation traces have identical content fingerprints"
        )
    return {
        "trace_version": TRACE_VERSION,
        "training_trace_sha256": training_fingerprint,
        "evaluation_trace_sha256": evaluation_fingerprint,
        "training_episode_count": len(training_ids),
        "evaluation_episode_count": len(evaluation_ids),
        "training_scenarios": sorted(set(training["scenario"].astype(str))),
        "evaluation_scenarios": sorted(set(evaluation["scenario"].astype(str))),
        "training_scenario_episode_counts": {
            str(scenario): int(group["episode_id"].nunique())
            for scenario, group in training.groupby("scenario", sort=True)
        },
        "evaluation_scenario_episode_counts": {
            str(scenario): int(group["episode_id"].nunique())
            for scenario, group in evaluation.groupby("scenario", sort=True)
        },
    }


def trace_fingerprint(frame: pd.DataFrame) -> str:
    """Return a stable SHA-256 fingerprint for the validated trace contents."""
    validated = validate_synchronized_trace(frame)
    encoded = validated.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_synchronized_trace(frame: pd.DataFrame, csv_path: str | Path) -> Path:
    """Validate and atomically write a synchronized trace CSV."""
    validated = validate_synchronized_trace(frame)
    destination = Path(csv_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
            validated.to_csv(temporary_file, index=False, lineterminator="\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic synchronized three-path RL trace"
    )
    scenario_group = parser.add_mutually_exclusive_group()
    scenario_group.add_argument("--scenario", choices=SUPPORTED_SCENARIOS)
    scenario_group.add_argument(
        "--mixture",
        nargs="+",
        metavar="SCENARIO=EPISODES",
        help="Generate a controlled mixture, for example iid=100 barrage=30",
    )
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/processed/rl_trace_iid.csv"),
    )
    args = parser.parse_args()
    if args.mixture:
        episode_counts: dict[str, int] = {}
        for item in args.mixture:
            scenario, separator, raw_count = item.partition("=")
            if not separator or scenario in episode_counts:
                parser.error(
                    "--mixture entries must be unique SCENARIO=EPISODES values"
                )
            try:
                episode_counts[scenario] = int(raw_count)
            except ValueError:
                parser.error(f"invalid episode count in --mixture entry {item!r}")
        try:
            frame = generate_scenario_mixture_trace(
                episode_counts=episode_counts,
                steps_per_episode=args.steps,
                base_seed=args.seed,
            )
        except ValueError as exc:
            parser.error(str(exc))
        episode_total = sum(episode_counts.values())
        description = ", ".join(
            f"{scenario}={count}" for scenario, count in episode_counts.items()
        )
    else:
        scenario = args.scenario or "iid"
        frame = generate_synchronized_trace(
            scenario=scenario,
            n_episodes=args.episodes,
            steps_per_episode=args.steps,
            base_seed=args.seed,
        )
        episode_total = args.episodes
        description = scenario
    destination = write_synchronized_trace(frame, args.output)
    print(
        f"Wrote {len(frame)} rows ({episode_total} episodes; {description}) "
        f"to {destination} "
        f"[sha256:{trace_fingerprint(frame)[:12]}]"
    )


if __name__ == "__main__":
    main()
