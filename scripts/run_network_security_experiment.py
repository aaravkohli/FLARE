#!/usr/bin/env python3
"""Frozen controlled-simulation study for the explicit network detectors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import yaml

from provenance import promote_run, write_experiment_run
from security.network_detector import NetworkThreatAnalyzer


BASE = Path(__file__).parent.parent
PROTOCOL = "controlled_network_security_v2"
PROFILES = (
    "normal", "policy_hold", "dos", "identity_spoofing",
    "metric_falsification", "replay",
)


def _sample(profile: str, rng: np.random.Generator, timestamp: float) -> tuple[dict, float]:
    received = int(rng.integers(90, 121))
    forwarded = max(0, received - int(rng.integers(0, 5)))
    reported = forwarded
    evidence = {
        "reported_tx_packets": received,
        "controller_rx_packets": received,
        "controller_forwarded_packets": forwarded,
        "reported_forwarded_packets": reported,
        "controller_dropped_packets": received - forwarded,
        "controller_policy_dropped_packets": 0,
        "controller_port_rx_packets": received,
        "controller_port_dropped_packets": received - forwarded,
        "controller_port_error_packets": 0,
        "controller_port_window_rx_packets": received,
        "controller_port_window_dropped_packets": received - forwarded,
        "controller_port_window_error_packets": 0,
        "port_timestamp": timestamp,
        "port_window_seconds": 1.0,
        "drop_counter_semantics": "ingress_port_receive_drop_error",
        "packet_rate_per_s": float(rng.uniform(20.0, 90.0)),
        "control_messages_per_s": float(rng.uniform(2.0, 12.0)),
        "duplicate_sequence_ratio": float(rng.uniform(0.0, 0.04)),
        "timestamp": timestamp,
        "controller_timestamp": timestamp,
        "observed_source_mac": "00:00:00:00:00:02",
        "expected_source_mac": "00:00:00:00:00:02",
        "provenance": {
            "category": "synthetic_simulation",
            "observer_id": "network-security-evaluation-harness",
            "independent": False,
            "collected_at": timestamp,
            "age_s": 0.0,
        },
    }
    latency = float(rng.uniform(15.0, 80.0))
    if profile == "policy_hold":
        evidence.update({
            "controller_forwarded_packets": 0,
            "reported_forwarded_packets": 0,
            "controller_policy_dropped_packets": received,
            "controller_dropped_packets": 0,
            "controller_port_dropped_packets": 0,
            "controller_port_window_dropped_packets": 0,
        })
    elif profile == "dos":
        attack_dropped = int(rng.integers(120, 201))
        evidence.update({
            "controller_forwarded_packets": int(rng.integers(0, 12)),
            "controller_dropped_packets": attack_dropped,
            "controller_port_dropped_packets": attack_dropped,
            "controller_port_window_dropped_packets": attack_dropped,
            "packet_rate_per_s": float(rng.uniform(550.0, 950.0)),
        })
        latency = float(rng.uniform(850.0, 1200.0))
    elif profile == "identity_spoofing":
        evidence["observed_source_mac"] = "02:ff:ff:ff:ff:fe"
    elif profile == "metric_falsification":
        evidence["reported_forwarded_packets"] = received
        evidence["controller_forwarded_packets"] = int(rng.integers(5, 30))
        evidence["duplicate_sequence_ratio"] = float(rng.uniform(0.22, 0.35))
    elif profile == "replay":
        evidence["reported_forwarded_packets"] = received
        evidence["controller_forwarded_packets"] = int(rng.integers(30, 55))
        evidence["duplicate_sequence_ratio"] = float(rng.uniform(0.35, 0.75))
    return evidence, latency


def run(*, seeds: list[int], samples_per_profile: int) -> dict:
    config = yaml.safe_load((BASE / "config" / "security_config.yaml").read_text())
    records = []
    tp = fp = tn = fn = unavailable_count = 0
    for seed in seeds:
        rng = np.random.default_rng(seed)
        analyzer = NetworkThreatAnalyzer({
            **config["network_detection"],
            # Each frozen sample is evaluated independently; temporal behavior
            # is covered separately by detector unit tests.
            "history_alpha": 0.0,
        })
        for profile in PROFILES:
            for index in range(samples_per_profile):
                timestamp = 1_800_000_000.0 + seed * 1000 + index
                evidence, latency = _sample(profile, rng, timestamp)
                analysis = analyzer.analyze(
                    f"sample_{seed}_{profile}_{index}",
                    evidence,
                    max_path_latency_ms=latency,
                    now=timestamp,
                )
                actual = profile not in {"normal", "policy_hold"}
                unavailable = analysis.status == "UNAVAILABLE"
                unavailable_count += int(unavailable)
                detected = analysis.status in {"SUSPICIOUS", "MALICIOUS"}
                tp += int(actual and detected)
                fp += int(not actual and detected)
                tn += int(not actual and not detected and not unavailable)
                fn += int(actual and not detected)
                records.append({
                    "seed": seed,
                    "sample": index,
                    "ground_truth": profile,
                    "status": analysis.status,
                    "detected_classes": list(analysis.detected_classes),
                    "risk_score": analysis.risk_score,
                })
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    false_positive_rate = fp / max(fp + tn, 1)
    passed = (
        unavailable_count == 0
        and precision >= 0.90
        and recall >= 0.90
        and false_positive_rate <= 0.05
    )
    return {
        "experiment": PROTOCOL,
        "evidence_category": "controlled_simulation",
        "seeds": seeds,
        "samples_per_profile": samples_per_profile,
        "profiles": list(PROFILES),
        "binary_detection": {
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
            "unavailable_count": unavailable_count,
            "precision": precision,
            "recall": recall,
            "false_positive_rate": false_positive_rate,
        },
        "promotion_gate": {
            "precision_min": 0.90,
            "recall_min": 0.90,
            "false_positive_rate_max": 0.05,
            "unavailable_count_max": 0,
            "passed": passed,
        },
        "limitations": [
            "Controlled synthetic counter distributions only.",
            "Does not establish real-network, RF, GPS, or field accuracy.",
        ],
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 99])
    parser.add_argument("--samples-per-profile", type=int, default=50)
    parser.add_argument(
        "--output", type=Path,
        default=BASE / "results" / "network_security_experiment.json",
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    report = run(seeds=args.seeds, samples_per_profile=args.samples_per_profile)
    artifact = write_experiment_run(
        base=BASE,
        experiment="network_security",
        protocol_version=PROTOCOL,
        report=report,
        seeds=args.seeds,
        evidence_category="controlled_simulation",
        config_paths=[Path("config/security_config.yaml")],
        parameters={"samples_per_profile": args.samples_per_profile},
        status="passed" if report["promotion_gate"]["passed"] else "failed",
    )
    if args.promote:
        if not report["promotion_gate"]["passed"]:
            raise SystemExit("promotion blocked: detector quality gate failed")
        promote_run(
            base=BASE,
            run_dir=artifact.run_dir,
            published_path=args.output,
            expected_experiment="network_security",
            expected_protocol=PROTOCOL,
        )
    print(json.dumps({
        "metrics": report["binary_detection"],
        "promotion_gate": report["promotion_gate"],
        "run_dir": str(artifact.run_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
