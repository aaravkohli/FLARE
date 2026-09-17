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
    SECURE_REWARD_DEFINITION,
)
from schemas.contracts import ROUTING_ACTIONS_V3, ROUTING_STATE_V3


CHECKPOINT_METADATA_VERSION = 3
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


def _reward_contract_sha256(
    reward_definition: str = REWARD_DEFINITION,
    observation_definition: str = OBSERVATION_DEFINITION,
) -> str:
    """Bind a policy to every configured/static value used by its reward."""
    contract: dict[str, Any] = {
        "reward_definition": reward_definition,
        "weights": asdict(DEFAULT_REWARD_WEIGHTS),
        "path_names": list(PATH_NAMES),
        "path_energy_costs": list(PATH_ENERGY_COSTS),
        "max_latency_ms": list(MAX_LATENCY_MS),
    }
    if observation_definition == ROUTING_STATE_V3:
        contract["path_names"] = list(ROUTING_ACTIONS_V3)
        contract["hold_penalty"] = 0.35
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
    observation_definition: str = OBSERVATION_DEFINITION,
    reward_definition: str | None = None,
) -> Path:
    """Atomically write metadata bound to the exact checkpoint contents."""
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint not found at {checkpoint}")

    resolved_reward = reward_definition or (
        SECURE_REWARD_DEFINITION
        if observation_definition == ROUTING_STATE_V3
        else REWARD_DEFINITION
    )
    metadata = {
        "metadata_version": CHECKPOINT_METADATA_VERSION,
        "algorithm": algorithm.lower(),
        "reward_definition": resolved_reward,
        "reward_contract_sha256": _reward_contract_sha256(
            resolved_reward, observation_definition
        ),
        "observation_definition": observation_definition,
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
    observation_definition: str = OBSERVATION_DEFINITION,
    reward_definition: str | None = None,
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

    resolved_reward = reward_definition or (
        SECURE_REWARD_DEFINITION
        if observation_definition == ROUTING_STATE_V3
        else REWARD_DEFINITION
    )
    expected = {
        "algorithm": algorithm.lower(),
        "reward_definition": resolved_reward,
        "reward_contract_sha256": _reward_contract_sha256(
            resolved_reward, observation_definition
        ),
        "observation_definition": observation_definition,
        "observation_dim": int(observation_dim),
        "action_count": int(action_count),
        "max_episode_steps": int(max_episode_steps),
    }
    metadata_version = metadata.get("metadata_version")
    compatible_versions = (
        {2, CHECKPOINT_METADATA_VERSION}
        if observation_definition == OBSERVATION_DEFINITION
        else {CHECKPOINT_METADATA_VERSION}
    )
    if metadata_version not in compatible_versions:
        raise CheckpointCompatibilityError(
            f"checkpoint metadata is incompatible: metadata_version={metadata_version!r} "
            f"(expected one of {sorted(compatible_versions)!r})"
        )
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
