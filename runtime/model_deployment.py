"""Atomic, hash-bound model deployment manifest handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Mapping


DEPLOYMENT_SCHEMA = "flare_model_deployment_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256(destination) != _sha256(source):
            raise ValueError(f"immutable deployment artifact collision: {destination}")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False,
        ) as handle, source.open("rb") as source_handle:
            shutil.copyfileobj(source_handle, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _relative_to_base(base: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(base.resolve()))
    except ValueError as exc:
        raise ValueError("deployed checkpoints must live inside the repository") from exc


def _validate_entry(base: Path, name: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    if entry.get("immutable") is not True:
        raise ValueError(f"{name} deployment entry is not immutable")
    path = base / str(entry["path"])
    metadata_path = base / str(entry["metadata_path"])
    expected_root = (base / "models" / "deployments" / name).resolve()
    try:
        path.resolve().relative_to(expected_root)
        metadata_path.resolve().relative_to(expected_root)
    except ValueError as exc:
        raise ValueError(f"{name} deployment paths are outside immutable storage") from exc
    if not path.is_file() or not metadata_path.is_file():
        raise ValueError(f"{name} checkpoint or metadata is missing")
    if _sha256(path) != entry.get("checkpoint_sha256"):
        raise ValueError(f"{name} checkpoint hash mismatch")
    if _sha256(metadata_path) != entry.get("metadata_sha256"):
        raise ValueError(f"{name} metadata hash mismatch")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("checkpoint_sha256") != entry.get("checkpoint_sha256"):
        raise ValueError(f"{name} metadata is not bound to the checkpoint")
    return {**entry, "resolved_path": path, "resolved_metadata_path": metadata_path}


def load_manifest(base: Path, manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != DEPLOYMENT_SCHEMA:
        raise ValueError("unsupported deployment manifest schema")
    generation = payload.get("generation")
    if not isinstance(generation, int) or generation < 1:
        raise ValueError("deployment generation must be a positive integer")
    models = payload.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("deployment manifest must contain at least one model")
    validated = {
        name: _validate_entry(base, name, entry)
        for name, entry in models.items()
    }
    return {**payload, "models": validated}


def publish_checkpoint(
    *,
    base: Path,
    manifest_path: Path,
    model_name: str,
    checkpoint_path: Path,
    metadata_path: Path,
    contract: str,
    provenance_run_id: str,
) -> dict[str, Any]:
    """Validate one immutable artifact and atomically publish a new generation."""
    if model_name not in {"fl", "routing"}:
        raise ValueError("model_name must be 'fl' or 'routing'")
    checkpoint_path = checkpoint_path.resolve()
    metadata_path = metadata_path.resolve()
    checkpoint_hash = _sha256(checkpoint_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("checkpoint_sha256") != checkpoint_hash:
        raise ValueError("checkpoint metadata hash does not match artifact")
    if not provenance_run_id.strip():
        raise ValueError("provenance_run_id must identify an immutable evidence run")
    if model_name == "fl":
        if contract != "threat_model_v2":
            raise ValueError("FL deployments require the threat_model_v2 contract")
        expected = {
            "data_definition": "grouped_temporal_v1",
            "sequence_length": 10,
            "input_features": 5,
            "num_paths": 3,
        }
    else:
        if contract not in {"routing_state_v2", "routing_state_v3"}:
            raise ValueError("unsupported routing deployment contract")
        expected = {
            "algorithm": "dqn",
            "observation_definition": contract,
            "observation_dim": 14 if contract == "routing_state_v2" else 18,
            "action_count": 3 if contract == "routing_state_v2" else 4,
        }
    mismatches = [
        f"{key}={metadata.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if metadata.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            f"{model_name} checkpoint metadata violates {contract}: "
            + "; ".join(mismatches)
        )
    suffix = "".join(checkpoint_path.suffixes) or ".bin"
    artifact_dir = base / "models" / "deployments" / model_name / checkpoint_hash
    immutable_checkpoint = artifact_dir / f"checkpoint{suffix}"
    immutable_metadata = artifact_dir / "checkpoint.metadata.json"
    _atomic_copy(checkpoint_path, immutable_checkpoint)
    _atomic_copy(metadata_path, immutable_metadata)
    current: dict[str, Any] = {}
    generation = 1
    if manifest_path.is_file():
        current = load_manifest(base, manifest_path)
        generation = int(current["generation"]) + 1
    models = {
        name: {
            key: value for key, value in entry.items()
            if key not in {"resolved_path", "resolved_metadata_path"}
        }
        for name, entry in current.get("models", {}).items()
    }
    promoted_at = datetime.now(timezone.utc).isoformat()
    models[model_name] = {
        "path": _relative_to_base(base, immutable_checkpoint),
        "metadata_path": _relative_to_base(base, immutable_metadata),
        "checkpoint_sha256": checkpoint_hash,
        "metadata_sha256": _sha256(metadata_path),
        "contract": contract,
        "contract_metadata": expected,
        "provenance_run_id": provenance_run_id,
        "immutable": True,
        "promoted_at_utc": promoted_at,
        "generation": generation,
    }
    payload = {
        "schema_version": DEPLOYMENT_SCHEMA,
        "generation": generation,
        "promoted_at_utc": promoted_at,
        "models": models,
    }
    _atomic_json(manifest_path, payload)
    return load_manifest(base, manifest_path)


@dataclass
class DeploymentWatcher:
    base: Path
    manifest_path: Path
    poll_interval_s: float = 0.0
    active_generation: int = 0
    failed_generation: int | None = None
    last_error: str | None = None
    _last_checked_monotonic: float = 0.0

    def candidate(self) -> dict[str, Any] | None:
        now = time.monotonic()
        if now - self._last_checked_monotonic < max(0.0, self.poll_interval_s):
            return None
        self._last_checked_monotonic = now
        if not self.manifest_path.is_file():
            return None
        try:
            manifest = load_manifest(self.base, self.manifest_path)
        except Exception as exc:
            self.last_error = str(exc)
            return None
        generation = int(manifest["generation"])
        if generation <= self.active_generation or generation == self.failed_generation:
            return None
        return manifest

    def activate(self, generation: int) -> None:
        self.active_generation = int(generation)
        self.failed_generation = None
        self.last_error = None

    def reject(self, generation: int, error: Exception | str) -> None:
        self.failed_generation = int(generation)
        self.last_error = str(error)

    def status(self) -> dict[str, Any]:
        return {
            "active_generation": self.active_generation,
            "failed_generation": self.failed_generation,
            "last_error": self.last_error,
            "manifest_path": str(self.manifest_path),
        }
