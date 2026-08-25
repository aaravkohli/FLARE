"""Versioned metadata and compatibility checks for deployable RL checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rl.reward import (
    DEFAULT_REWARD_WEIGHTS,
    MAX_LATENCY_MS,
    PATH_ENERGY_COSTS,
    PATH_NAMES,
    REWARD_DEFINITION,
)


CHECKPOINT_METADATA_VERSION = 2
OBSERVATION_DEFINITION = "routing_state_v2"
_HASH_CHUNK_SIZE = 1024 * 1024


class CheckpointCompatibilityError(RuntimeError):
    """Raised when a checkpoint cannot safely be used by this runtime."""


def checkpoint_metadata_path(checkpoint_path: str | Path) -> Path:
    """Return the sidecar path without changing the checkpoint's own suffix."""
    checkpoint = Path(checkpoint_path)
    return checkpoint.with_name(f"{checkpoint.name}.metadata.json")


def _checkpoint_sha256(checkpoint_path: Path) -> str:
    digest = hashlib.sha256()
    with checkpoint_path.open("rb") as checkpoint_file:
        for chunk in iter(lambda: checkpoint_file.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reward_contract_sha256() -> str:
    """Bind a policy to every configured/static value used by its reward."""
    contract = {
        "reward_definition": REWARD_DEFINITION,
        "weights": asdict(DEFAULT_REWARD_WEIGHTS),
        "path_names": list(PATH_NAMES),
        "path_energy_costs": list(PATH_ENERGY_COSTS),
        "max_latency_ms": list(MAX_LATENCY_MS),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_checkpoint_metadata(
    checkpoint_path: str | Path,
    *,
    algorithm: str,
    observation_dim: int,
    action_count: int,
    max_episode_steps: int,
    training_timesteps: int,
    training_mode: str,
    training_seed: int,
    training_provenance: Mapping[str, Any] | None = None,
) -> Path:
    """Atomically write metadata bound to the exact checkpoint contents."""
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint not found at {checkpoint}")

    metadata = {
        "metadata_version": CHECKPOINT_METADATA_VERSION,
        "algorithm": algorithm.lower(),
        "reward_definition": REWARD_DEFINITION,
        "reward_contract_sha256": _reward_contract_sha256(),
        "observation_definition": OBSERVATION_DEFINITION,
        "observation_dim": int(observation_dim),
        "action_count": int(action_count),
        "max_episode_steps": int(max_episode_steps),
        "training_timesteps": int(training_timesteps),
        "training_mode": str(training_mode),
        "training_seed": int(training_seed),
        "checkpoint_sha256": _checkpoint_sha256(checkpoint),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if training_provenance is not None:
        metadata["training_provenance"] = dict(training_provenance)
    sidecar = checkpoint_metadata_path(checkpoint)
    sidecar.parent.mkdir(parents=True, exist_ok=True)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=sidecar.parent,
            prefix=f".{sidecar.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
            json.dump(metadata, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, sidecar)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    return sidecar


def validate_checkpoint_metadata(
    checkpoint_path: str | Path,
    *,
    algorithm: str,
    observation_dim: int,
    action_count: int,
    max_episode_steps: int,
) -> Mapping[str, Any]:
    """Validate semantic compatibility and return the parsed metadata."""
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"RL model checkpoint not found at {checkpoint}")

    sidecar = checkpoint_metadata_path(checkpoint)
    if not sidecar.is_file():
        raise CheckpointCompatibilityError(
            f"checkpoint metadata not found at {sidecar}; retrain the policy "
            f"under reward definition {REWARD_DEFINITION}"
        )

    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointCompatibilityError(
            f"checkpoint metadata at {sidecar} is unreadable or invalid"
        ) from exc
    if not isinstance(metadata, dict):
        raise CheckpointCompatibilityError("checkpoint metadata must be a JSON object")

    expected = {
        "metadata_version": CHECKPOINT_METADATA_VERSION,
        "algorithm": algorithm.lower(),
        "reward_definition": REWARD_DEFINITION,
        "reward_contract_sha256": _reward_contract_sha256(),
        "observation_definition": OBSERVATION_DEFINITION,
        "observation_dim": int(observation_dim),
        "action_count": int(action_count),
        "max_episode_steps": int(max_episode_steps),
    }
    mismatches = [
        f"{key}={metadata.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if metadata.get(key) != value
    ]
    if mismatches:
        raise CheckpointCompatibilityError(
            "checkpoint metadata is incompatible: " + "; ".join(mismatches)
        )

    recorded_hash = metadata.get("checkpoint_sha256")
    actual_hash = _checkpoint_sha256(checkpoint)
    if recorded_hash != actual_hash:
        raise CheckpointCompatibilityError(
            "checkpoint SHA-256 does not match its metadata; the checkpoint or "
            "sidecar may have been replaced"
        )

    return metadata
