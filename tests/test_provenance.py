"""Evidence-gated immutable result artifact tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import provenance.artifacts as artifacts
from provenance.artifacts import (
    promote_run,
    verify_promoted_artifact,
    write_experiment_run,
)
from scripts.update_readme_metrics import END, START, render_block, update_readme


def _input_files(base: Path) -> tuple[Path, Path, Path]:
    config = base / "config.yaml"
    checkpoint = base / "model.bin"
    dataset = base / "evaluation.csv"
    config.write_text("threshold: 0.5\n", encoding="utf-8")
    checkpoint.write_bytes(b"checkpoint")
    dataset.write_text("value\n1\n", encoding="utf-8")
    return config, checkpoint, dataset


def test_repeated_runs_are_immutable_and_capture_inputs(tmp_path):
    config, checkpoint, dataset = _input_files(tmp_path)
    arguments = dict(
        base=tmp_path,
        experiment="detector",
        protocol_version="detector_v1",
        report={"f1": 0.95},
        seeds=[7, 42, 99],
        evidence_category="controlled_simulation",
        config_paths=[config],
        checkpoint_paths=[checkpoint],
        dataset_paths=[dataset],
        command=["python", "experiment.py"],
    )
    first = write_experiment_run(**arguments)
    second = write_experiment_run(**arguments)

    assert first.run_dir != second.run_dir
    assert first.report_path.read_text() == second.report_path.read_text()
    manifest = json.loads(first.manifest_path.read_text())
    assert manifest["schema_version"] == "flare_experiment_manifest_v1"
    assert manifest["seeds"] == [7, 42, 99]
    assert manifest["datasets"][dataset.name]["sha256"]
    assert manifest["checkpoints"][checkpoint.name]["sha256"]
    assert "dirty_state_sha256" in manifest["git"]


def test_single_seed_cannot_replace_published_multi_seed_result(tmp_path):
    config, checkpoint, dataset = _input_files(tmp_path)
    run = write_experiment_run(
        base=tmp_path,
        experiment="detector",
        protocol_version="detector_v1",
        report={"seed": 42},
        seeds=[42],
        evidence_category="controlled_simulation",
        config_paths=[config],
        checkpoint_paths=[checkpoint],
        dataset_paths=[dataset],
    )

    with pytest.raises(ValueError, match="ordered seeds"):
        promote_run(
            base=tmp_path,
            run_dir=run.run_dir,
            published_path=Path("results/detector.json"),
            expected_experiment="detector",
            expected_protocol="detector_v1",
        )
    assert not (tmp_path / "results" / "detector.json").exists()


@pytest.mark.parametrize(
    ("status", "protocol", "error"),
    [("failed", "detector_v1", "passed runs"), ("passed", "wrong_v2", "protocol")],
)
def test_failed_or_wrong_protocol_run_cannot_be_promoted(
    tmp_path, status, protocol, error
):
    run = write_experiment_run(
        base=tmp_path,
        experiment="detector",
        protocol_version=protocol,
        report={"result": status},
        seeds=[7, 42, 99],
        evidence_category="controlled_simulation",
        status=status,
    )
    with pytest.raises(ValueError, match=error):
        promote_run(
            base=tmp_path,
            run_dir=run.run_dir,
            published_path=Path("results/detector.json"),
            expected_experiment="detector",
            expected_protocol="detector_v1",
        )


def test_promotion_rejects_changed_inputs_and_publishes_atomically(tmp_path):
    config, checkpoint, dataset = _input_files(tmp_path)
    run = write_experiment_run(
        base=tmp_path,
        experiment="detector",
        protocol_version="detector_v1",
        report={"result": "verified"},
        seeds=[7, 42, 99],
        evidence_category="controlled_simulation",
        config_paths=[config],
        checkpoint_paths=[checkpoint],
        dataset_paths=[dataset],
    )
    dataset.write_text("value\n2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="input hash"):
        promote_run(
            base=tmp_path,
            run_dir=run.run_dir,
            published_path=Path("results/detector.json"),
            expected_experiment="detector",
            expected_protocol="detector_v1",
        )

    dataset.write_text("value\n1\n", encoding="utf-8")
    destination = promote_run(
        base=tmp_path,
        run_dir=run.run_dir,
        published_path=Path("results/detector.json"),
        expected_experiment="detector",
        expected_protocol="detector_v1",
    )
    assert json.loads(destination.read_text()) == {"result": "verified"}
    assert destination.with_name("detector.json.provenance.json").is_file()
    report, manifest = verify_promoted_artifact(
        base=tmp_path,
        published_path=Path("results/detector.json"),
        expected_experiment="detector",
        expected_protocol="detector_v1",
    )
    assert report == {"result": "verified"}
    assert manifest["exit_status"] == 0


def test_promotion_rejects_changed_dirty_source_fingerprint(tmp_path, monkeypatch):
    state = {"revision": "abc", "dirty": True, "dirty_state_sha256": "first"}
    monkeypatch.setattr(artifacts, "git_provenance", lambda _base: dict(state))
    run = write_experiment_run(
        base=tmp_path,
        experiment="detector",
        protocol_version="detector_v1",
        report={"result": "verified"},
        seeds=[7, 42, 99],
        evidence_category="controlled_simulation",
    )
    state["dirty_state_sha256"] = "changed"
    with pytest.raises(ValueError, match="dirty-state"):
        promote_run(
            base=tmp_path,
            run_dir=run.run_dir,
            published_path=Path("results/detector.json"),
            expected_experiment="detector",
            expected_protocol="detector_v1",
        )


def test_readme_evidence_update_requires_valid_promoted_artifact(tmp_path):
    config, checkpoint, dataset = _input_files(tmp_path)
    run = write_experiment_run(
        base=tmp_path,
        experiment="detector",
        protocol_version="detector_v1",
        report={"result": "verified"},
        seeds=[7, 42, 99],
        evidence_category="controlled_simulation",
        config_paths=[config],
        checkpoint_paths=[checkpoint],
        dataset_paths=[dataset],
    )
    published = promote_run(
        base=tmp_path,
        run_dir=run.run_dir,
        published_path=Path("results/detector.json"),
        expected_experiment="detector",
        expected_protocol="detector_v1",
    )
    readme = tmp_path / "README.md"
    readme.write_text(f"before\n{START}\nold\n{END}\nafter\n")
    block = render_block(tmp_path, [("Detector", str(published))])
    update_readme(readme, block)
    assert run.run_dir.name in readme.read_text()

    published.write_text("{}\n")
    with pytest.raises(ValueError, match="hash"):
        render_block(tmp_path, [("Detector", str(published))])


def test_generated_readme_evidence_rows_do_not_change_source_fingerprint(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(f"before\n{START}\nold\n{END}\nafter\n")
    before = artifacts._source_fingerprint(readme, "README.md")
    update_readme(readme, f"{START}\nnew generated row\n{END}")
    assert artifacts._source_fingerprint(readme, "README.md") == before

    readme.write_text(readme.read_text().replace("before", "changed"))
    assert artifacts._source_fingerprint(readme, "README.md") != before
