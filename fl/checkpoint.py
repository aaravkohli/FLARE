"""Compatibility metadata for FL models trained on temporal RF windows."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fl.data import TEMPORAL_DATA_DEFINITION


FL_CHECKPOINT_METADATA_VERSION = 1
_HASH_CHUNK_SIZE = 1024 * 1024


class FLCheckpointCompatibilityError(RuntimeError):
    """Raised when an FL checkpoint does not match the runtime data contract."""


def fl_checkpoint_metadata_path(checkpoint_path: str | Path) -> Path:
    checkpoint = Path(checkpoint_path)
    return checkpoint.with_name(f"{checkpoint.name}.metadata.json")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_sha256(config: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(config), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_fl_checkpoint_metadata(
    checkpoint_path: str | Path,
    *,
    model_config: Mapping[str, Any],
    data_config: Mapping[str, Any],
    federation_round: int,
) -> Path:
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"FL checkpoint not found at {checkpoint}")

    metadata = {
        "metadata_version": FL_CHECKPOINT_METADATA_VERSION,
        "data_definition": TEMPORAL_DATA_DEFINITION,
        "sequence_length": int(model_config["sequence_len"]),
        "sequence_stride": int(data_config.get("sequence_stride", 1)),
        "input_features": int(model_config["input_features"]),
        "num_paths": int(model_config["num_paths"]),
        "num_attack_classes": int(model_config["num_attack_classes"]),
        "model_config_sha256": _config_sha256(model_config),
        "checkpoint_sha256": _file_sha256(checkpoint),
        "federation_round": int(federation_round),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    sidecar = fl_checkpoint_metadata_path(checkpoint)
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


def validate_fl_checkpoint_metadata(
    checkpoint_path: str | Path,
    *,
    model_config: Mapping[str, Any],
    data_config: Mapping[str, Any],
) -> Mapping[str, Any]:
    checkpoint = Path(checkpoint_path)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"FL checkpoint not found at {checkpoint}")
    sidecar = fl_checkpoint_metadata_path(checkpoint)
    if not sidecar.is_file():
        raise FLCheckpointCompatibilityError(
            f"FL checkpoint metadata not found at {sidecar}; retrain under "
            f"data definition {TEMPORAL_DATA_DEFINITION}"
        )
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FLCheckpointCompatibilityError(
            f"FL checkpoint metadata at {sidecar} is unreadable or invalid"
        ) from exc
    if not isinstance(metadata, dict):
        raise FLCheckpointCompatibilityError("FL checkpoint metadata must be a JSON object")

    expected = {
        "metadata_version": FL_CHECKPOINT_METADATA_VERSION,
        "data_definition": TEMPORAL_DATA_DEFINITION,
        "sequence_length": int(model_config["sequence_len"]),
        "sequence_stride": int(data_config.get("sequence_stride", 1)),
        "input_features": int(model_config["input_features"]),
        "num_paths": int(model_config["num_paths"]),
        "num_attack_classes": int(model_config["num_attack_classes"]),
        "model_config_sha256": _config_sha256(model_config),
    }
    mismatches = [
        f"{key}={metadata.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if metadata.get(key) != value
    ]
    if mismatches:
        raise FLCheckpointCompatibilityError(
            "FL checkpoint metadata is incompatible: " + "; ".join(mismatches)
        )
    if metadata.get("checkpoint_sha256") != _file_sha256(checkpoint):
        raise FLCheckpointCompatibilityError(
            "FL checkpoint SHA-256 does not match its metadata"
        )
    return metadata
