"""One-command FLARE security regression and provenance manifest.

Quick mode exercises every Phase 0–7 contract. ``--full`` additionally runs
the published multi-seed Byzantine and IID/non-IID experiment suites.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from provenance import promote_run, write_experiment_run


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str]) -> dict:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=BASE,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "duration_s": round(time.perf_counter() - started, 3),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE / "results" / "security_regression_manifest.json",
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    test_targets = [
        "tests/test_byzantine_aggregation.py",
        "tests/test_non_iid_fl.py",
        "tests/test_security_phases_0_7.py",
        "tests/test_phase10_constrained_routing.py",
        "tests/test_phase11_sdn_correctness.py",
    ]
    commands = [[sys.executable, "-m", "pytest", "-q", *test_targets]]
    if args.full:
        commands.extend([
            [sys.executable, "scripts/run_byzantine_experiment.py", "--rounds", "12", "--seeds", "7", "42", "99"],
            [sys.executable, "scripts/run_fl_resilience_matrix.py", "--rounds", "4", "--seeds", "7", "42", "99"],
        ])
    runs = [_run(command) for command in commands]
    manifest = {
        "baseline_contract": "flare_security_phases_0_7_v1",
        "created_unix_s": time.time(),
        "full": args.full,
        "passed": all(run["returncode"] == 0 for run in runs),
        "config_sha256": {
            str(path.relative_to(BASE)): _sha256(path)
            for path in (
                BASE / "config" / "fl_config.yaml",
                BASE / "config" / "rl_config.yaml",
                BASE / "config" / "sdn_config.yaml",
            )
        },
        "commands": runs,
    }
    run_artifact = write_experiment_run(
        base=BASE,
        experiment="security_regression",
        protocol_version="flare_security_regression_v2",
        report=manifest,
        seeds=[7, 42, 99] if args.full else [],
        evidence_category="controlled_simulation",
        config_paths=[
            Path("config/fl_config.yaml"),
            Path("config/rl_config.yaml"),
            Path("config/sdn_config.yaml"),
        ],
        status="passed" if manifest["passed"] else "failed",
        parameters={"full": args.full},
    )
    if args.promote:
        promote_run(
            base=BASE,
            run_dir=run_artifact.run_dir,
            published_path=args.output,
            expected_experiment="security_regression",
            expected_protocol="flare_security_regression_v2",
            required_seeds=[7, 42, 99] if args.full else [],
        )
    print(json.dumps({"passed": manifest["passed"], "manifest": str(run_artifact.manifest_path)}, indent=2))
    if not manifest["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
