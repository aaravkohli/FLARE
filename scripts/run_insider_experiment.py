"""Reproducible telemetry-insider detection and containment experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from fl.insider import InsiderTelemetryAnalyzer
from sdn.containment import ContainmentMode, decide_containment
from simulation.insider_state import generate_insider_evidence
from provenance import promote_run, write_experiment_run


BASE = Path(__file__).parent.parent
PROFILES = (
    "normal",
    "selective_forwarding",
    "telemetry_falsification",
    "control_flood",
    "replay",
)


def run(*, rounds: int, seeds: list[int]) -> dict:
    records = []
    tp = fp = tn = fn = 0
    for seed in seeds:
        analyzer = InsiderTelemetryAnalyzer(history_alpha=0.0)
        for profile_index, profile in enumerate(PROFILES):
            for round_index in range(rounds):
                evidence = generate_insider_evidence(
                    "drone_1",
                    seed=seed * 10_000 + profile_index * rounds + round_index,
                    timestamp=100.0 + round_index,
                    profile_override=profile,
                )
                analysis = analyzer.analyze("drone_1", evidence)
                actual = profile != "normal"
                detected = analysis.status != "NORMAL"
                tp += int(actual and detected)
                fp += int(not actual and detected)
                tn += int(not actual and not detected)
                fn += int(actual and not detected)
                containment = decide_containment(
                    "drone_1", trust_score=0.95, insider_risk=analysis.risk_score
                )
                records.append({
                    "seed": seed,
                    "round": round_index + 1,
                    "actual_profile": profile,
                    "predicted_class": analysis.predicted_class,
                    "status": analysis.status,
                    "risk_score": analysis.risk_score,
                    "containment": containment.mode.value,
                })
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    report = {
        "experiment": "controlled_insider_detection_v1",
        "seeds": seeds,
        "rounds_per_profile": rounds,
        "profiles": list(PROFILES),
        "binary_detection": {
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / max(precision + recall, 1e-12),
        },
        "mean_normal_risk": float(np.mean([
            row["risk_score"] for row in records if row["actual_profile"] == "normal"
        ])),
        "mean_attack_risk": float(np.mean([
            row["risk_score"] for row in records if row["actual_profile"] != "normal"
        ])),
        "quarantined_records": sum(
            row["containment"] == ContainmentMode.QUARANTINED.value for row in records
        ),
        "records": records,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 99])
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE / "results" / "insider_detection_experiment.json",
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    report = run(rounds=args.rounds, seeds=args.seeds)
    run_artifact = write_experiment_run(
        base=BASE,
        experiment="insider_detection",
        protocol_version="controlled_insider_detection_v1",
        report=report,
        seeds=args.seeds,
        evidence_category="controlled_simulation",
        config_paths=[Path("config/fl_config.yaml"), Path("config/sdn_config.yaml")],
        parameters={"rounds_per_profile": args.rounds},
    )
    if args.promote:
        promote_run(
            base=BASE,
            run_dir=run_artifact.run_dir,
            published_path=args.output,
            expected_experiment="insider_detection",
            expected_protocol="controlled_insider_detection_v1",
        )
    print(json.dumps({
        "binary_detection": report["binary_detection"],
        "mean_normal_risk": report["mean_normal_risk"],
        "mean_attack_risk": report["mean_attack_risk"],
        "output": str(run_artifact.report_path),
    }, indent=2))


if __name__ == "__main__":
    main()
