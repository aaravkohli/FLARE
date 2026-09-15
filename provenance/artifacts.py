"""Immutable FLARE experiment artifacts with verifiable provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence


MANIFEST_SCHEMA = "flare_experiment_manifest_v1"
DEFAULT_REQUIRED_SEEDS = (7, 42, 99)
EVIDENCE_CATEGORIES = {
    "synthetic",
    "controlled_simulation",
    "mock_sdn",
    "packet_simulation",
    "real_network",
    "hardware",
}
README_EVIDENCE_START = "<!-- PROMOTED-EVIDENCE:START -->"
README_EVIDENCE_END = "<!-- PROMOTED-EVIDENCE:END -->"


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(path: Path, relative_path: str) -> str | None:
    """Hash source files while ignoring the machine-generated README evidence rows."""
    if relative_path != "README.md" or not path.is_file():
        return sha256_file(path)
    contents = path.read_text(encoding="utf-8")
    if contents.count(README_EVIDENCE_START) != 1 or contents.count(README_EVIDENCE_END) != 1:
        return sha256_file(path)
    before, remainder = contents.split(README_EVIDENCE_START, 1)
    _generated, after = remainder.split(README_EVIDENCE_END, 1)
    normalized = before + README_EVIDENCE_START + README_EVIDENCE_END + after
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _git(base: Path, *arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments], cwd=base, text=True, capture_output=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def git_provenance(base: Path) -> dict[str, Any]:
    revision = _git(base, "rev-parse", "HEAD")
    status = _git(base, "status", "--porcelain=v1", "--untracked-files=all")
    status = status or ""
    records = []
    for line in status.splitlines():
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        # Generated evidence and runtime state must not invalidate the source
        # fingerprint merely because a run was saved or promoted.
        if raw_path.startswith(("results/", "runtime/", "models/deployments/")):
            continue
        path = base / raw_path
        records.append({
            "status": line[:2],
            "path": raw_path,
            "sha256": _source_fingerprint(path, raw_path),
            "size_bytes": path.stat().st_size if path.is_file() else None,
        })
    dirty_payload = _json_bytes(records)
    return {
        "revision": revision,
        "dirty": bool(records),
        "dirty_state_sha256": hashlib.sha256(dirty_payload).hexdigest(),
    }


def _file_records(base: Path, paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw_path in paths:
        path = raw_path if raw_path.is_absolute() else base / raw_path
        try:
            name = str(path.resolve().relative_to(base.resolve()))
        except ValueError:
            name = str(path.resolve())
        records[name] = {
            "path": name,
            "exists": path.is_file(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size if path.is_file() else None,
        }
    return records


@dataclass(frozen=True)
class ExperimentRun:
    run_dir: Path
    report_path: Path
    manifest_path: Path


def write_experiment_run(
    *,
    base: Path,
    experiment: str,
    protocol_version: str,
    report: Mapping[str, Any],
    seeds: Sequence[int],
    evidence_category: str,
    config_paths: Sequence[Path] = (),
    checkpoint_paths: Sequence[Path] = (),
    dataset_paths: Sequence[Path] = (),
    dataset_roles: Mapping[str, str] | None = None,
    dataset_fingerprints: Mapping[str, str] | None = None,
    command: Sequence[str] | None = None,
    status: str = "passed",
    parameters: Mapping[str, Any] | None = None,
    extra_files: Mapping[str, bytes | str] | None = None,
) -> ExperimentRun:
    """Write one immutable run directory and a hash-bound manifest."""
    if evidence_category not in EVIDENCE_CATEGORIES:
        raise ValueError(f"unsupported evidence category: {evidence_category!r}")
    safe_experiment = experiment.strip().replace("/", "_")
    if not safe_experiment or safe_experiment in {".", ".."}:
        raise ValueError("experiment must be a non-empty path-safe name")
    created_at = datetime.now(timezone.utc)
    identity = {
        "experiment": safe_experiment,
        "protocol_version": protocol_version,
        "seeds": [int(seed) for seed in seeds],
        "created_ns": time.time_ns(),
        "git": git_provenance(base),
    }
    identity_digest = hashlib.sha256(_json_bytes(identity)).hexdigest()[:12]
    run_id = f"{created_at.strftime('%Y%m%dT%H%M%S.%fZ')}-{identity_digest}"
    run_dir = base / "results" / "runs" / safe_experiment / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    report_path = run_dir / "report.json"
    _atomic_write(report_path, _json_bytes(dict(report)))
    written_extras: dict[str, dict[str, Any]] = {}
    for name, value in (extra_files or {}).items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe extra artifact name: {name!r}")
        payload = value.encode("utf-8") if isinstance(value, str) else value
        destination = run_dir / relative
        _atomic_write(destination, payload)
        written_extras[name] = {
            "path": name,
            "sha256": sha256_file(destination),
            "size_bytes": len(payload),
        }

    dataset_records = _file_records(base, dataset_paths)
    for name, record in dataset_records.items():
        path_key = str(record["path"])
        absolute_key = str((base / path_key).resolve()) if not Path(path_key).is_absolute() else path_key
        record["role"] = (
            (dataset_roles or {}).get(path_key)
            or (dataset_roles or {}).get(absolute_key)
            or "unspecified"
        )
        record["semantic_fingerprint"] = (
            (dataset_fingerprints or {}).get(path_key)
            or (dataset_fingerprints or {}).get(absolute_key)
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "experiment": safe_experiment,
        "protocol_version": protocol_version,
        "run_id": run_id,
        "created_at_utc": created_at.isoformat(),
        "command": list(command or [sys.executable, *sys.argv]),
        "python_executable": sys.executable,
        "git": identity["git"],
        "status": status,
        "exit_status": 0 if status == "passed" else 1,
        "seeds": [int(seed) for seed in seeds],
        "parameters": dict(parameters or {}),
        "evidence_category": evidence_category,
        "configurations": _file_records(base, config_paths),
        "checkpoints": _file_records(base, checkpoint_paths),
        "datasets": dataset_records,
        "report": {
            "path": "report.json",
            "sha256": sha256_file(report_path),
            "size_bytes": report_path.stat().st_size,
        },
        "extra_artifacts": written_extras,
    }
    manifest_path = run_dir / "manifest.json"
    _atomic_write(manifest_path, _json_bytes(manifest))
    return ExperimentRun(run_dir, report_path, manifest_path)


def _verify_records(base: Path, records: Mapping[str, Mapping[str, Any]]) -> None:
    for name, record in records.items():
        path = Path(str(record["path"]))
        path = path if path.is_absolute() else base / path
        if sha256_file(path) != record.get("sha256"):
            raise ValueError(f"current input hash does not match run manifest: {name}")


def promote_run(
    *,
    base: Path,
    run_dir: Path,
    published_path: Path,
    expected_experiment: str,
    expected_protocol: str,
    required_seeds: Sequence[int] = DEFAULT_REQUIRED_SEEDS,
    extra_publications: Mapping[str, Path] | None = None,
) -> Path:
    """Validate and atomically publish an immutable experiment report."""
    manifest_path = run_dir / "manifest.json"
    report_path = run_dir / "report.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("unsupported experiment manifest schema")
    if manifest.get("experiment") != expected_experiment:
        raise ValueError("experiment name does not match promotion target")
    if manifest.get("protocol_version") != expected_protocol:
        raise ValueError("experiment protocol does not match promotion target")
    if manifest.get("status") != "passed" or manifest.get("exit_status") != 0:
        raise ValueError("only passed runs may be promoted")
    if list(manifest.get("seeds", [])) != [int(seed) for seed in required_seeds]:
        raise ValueError(
            f"promotion requires ordered seeds {list(required_seeds)}; "
            f"found {manifest.get('seeds')}"
        )
    if sha256_file(report_path) != manifest.get("report", {}).get("sha256"):
        raise ValueError("report hash does not match its manifest")
    current_git = git_provenance(base)
    recorded_git = manifest.get("git", {})
    if (
        current_git.get("revision") != recorded_git.get("revision")
        or current_git.get("dirty_state_sha256")
        != recorded_git.get("dirty_state_sha256")
    ):
        raise ValueError("Git revision or dirty-state fingerprint changed since the run")
    for section in ("configurations", "checkpoints", "datasets"):
        _verify_records(base, manifest.get(section, {}))
    for artifact_name, target in (extra_publications or {}).items():
        record = manifest.get("extra_artifacts", {}).get(artifact_name)
        if not isinstance(record, Mapping):
            raise ValueError(f"extra artifact is absent from manifest: {artifact_name}")
        source = run_dir / str(record["path"])
        if sha256_file(source) != record.get("sha256"):
            raise ValueError(f"extra artifact hash mismatch: {artifact_name}")
        extra_destination = target if target.is_absolute() else base / target
        _atomic_write(extra_destination, source.read_bytes())

    destination = published_path if published_path.is_absolute() else base / published_path
    _atomic_write(destination, report_path.read_bytes())
    _atomic_write(
        destination.with_name(f"{destination.name}.provenance.json"),
        manifest_path.read_bytes(),
    )
    return destination


def verify_promoted_artifact(
    *,
    base: Path,
    published_path: Path,
    expected_experiment: str | None = None,
    expected_protocol: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a published report and its copied provenance manifest."""
    destination = published_path if published_path.is_absolute() else base / published_path
    provenance_path = destination.with_name(f"{destination.name}.provenance.json")
    if not destination.is_file() or not provenance_path.is_file():
        raise ValueError("published report or provenance sidecar is missing")
    manifest = json.loads(provenance_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("unsupported experiment manifest schema")
    if expected_experiment and manifest.get("experiment") != expected_experiment:
        raise ValueError("published experiment does not match expected experiment")
    if expected_protocol and manifest.get("protocol_version") != expected_protocol:
        raise ValueError("published protocol does not match expected protocol")
    if manifest.get("status") != "passed" or manifest.get("exit_status") != 0:
        raise ValueError("published artifact does not record a successful command")
    if sha256_file(destination) != manifest.get("report", {}).get("sha256"):
        raise ValueError("published report hash does not match provenance")
    current_git = git_provenance(base)
    recorded_git = manifest.get("git", {})
    if (
        current_git.get("revision") != recorded_git.get("revision")
        or current_git.get("dirty_state_sha256")
        != recorded_git.get("dirty_state_sha256")
    ):
        raise ValueError("published evidence is stale for the current Git/dirty state")
    for section in ("configurations", "checkpoints", "datasets"):
        _verify_records(base, manifest.get(section, {}))
    return json.loads(destination.read_text(encoding="utf-8")), manifest
