"""Atomic checkpoint deployment manifest tests."""

from __future__ import annotations

import hashlib
import json

from runtime.model_deployment import DeploymentWatcher, publish_checkpoint


def _checkpoint(tmp_path, name="model.bin"):
    checkpoint = tmp_path / name
    checkpoint.write_bytes(b"model-v1")
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    metadata = checkpoint.with_name(f"{checkpoint.name}.metadata.json")
    metadata.write_text(json.dumps({
        "checkpoint_sha256": digest,
        "data_definition": "grouped_temporal_v1",
        "sequence_length": 10,
        "input_features": 5,
        "num_paths": 3,
        "algorithm": "dqn",
        "observation_definition": "routing_state_v2",
        "observation_dim": 14,
        "action_count": 3,
    }), encoding="utf-8")
    return checkpoint, metadata


def test_promotion_is_hash_bound_and_generation_monotonic(tmp_path):
    checkpoint, metadata = _checkpoint(tmp_path)
    manifest = tmp_path / "models" / "deployment_manifest.json"
    first = publish_checkpoint(
        base=tmp_path,
        manifest_path=manifest,
        model_name="fl",
        checkpoint_path=checkpoint,
        metadata_path=metadata,
        contract="threat_model_v2",
        provenance_run_id="run-1",
    )
    second = publish_checkpoint(
        base=tmp_path,
        manifest_path=manifest,
        model_name="fl",
        checkpoint_path=checkpoint,
        metadata_path=metadata,
        contract="threat_model_v2",
        provenance_run_id="run-2",
    )
    assert first["generation"] == 1
    assert second["generation"] == 2
    assert second["models"]["fl"]["generation"] == 2
    assert second["models"]["fl"]["contract_metadata"]["input_features"] == 5


def test_promotion_rejects_metadata_contract_drift(tmp_path):
    checkpoint, metadata = _checkpoint(tmp_path)
    payload = json.loads(metadata.read_text())
    payload["observation_dim"] = 18
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    import pytest
    with pytest.raises(ValueError, match="observation_dim"):
        publish_checkpoint(
            base=tmp_path,
            manifest_path=tmp_path / "deployment.json",
            model_name="routing",
            checkpoint_path=checkpoint,
            metadata_path=metadata,
            contract="routing_state_v2",
            provenance_run_id="run-1",
        )


def test_watcher_retains_active_generation_when_candidate_is_invalid(tmp_path):
    checkpoint, metadata = _checkpoint(tmp_path)
    manifest = tmp_path / "deployment.json"
    publish_checkpoint(
        base=tmp_path,
        manifest_path=manifest,
        model_name="routing",
        checkpoint_path=checkpoint,
        metadata_path=metadata,
        contract="routing_state_v2",
        provenance_run_id="run-1",
    )
    watcher = DeploymentWatcher(tmp_path, manifest)
    candidate = watcher.candidate()
    assert candidate is not None
    watcher.activate(candidate["generation"])
    candidate["models"]["routing"]["resolved_path"].write_bytes(b"tampered")

    assert watcher.candidate() is None
    assert watcher.active_generation == 1
    assert "hash mismatch" in str(watcher.last_error)
