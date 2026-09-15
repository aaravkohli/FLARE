#!/usr/bin/env python3
"""Regenerate the README evidence block from promoted, hash-valid artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent))

from provenance import verify_promoted_artifact


BASE = Path(__file__).parent.parent
START = "<!-- PROMOTED-EVIDENCE:START -->"
END = "<!-- PROMOTED-EVIDENCE:END -->"
DEFAULT_ARTIFACTS = (
    ("Byzantine aggregation", "results/byzantine_experiment.json"),
    ("FL resilience matrix", "results/fl_resilience_matrix.json"),
    ("Traffic-insider detector", "results/insider_detection_experiment.json"),
    ("Network DoS/spoofing detector", "results/network_security_experiment.json"),
    ("Model and routing evaluation", "results/evaluation_report.json"),
)


def render_block(base: Path, artifacts: list[tuple[str, str]]) -> str:
    rows = []
    for label, relative in artifacts:
        _report, manifest = verify_promoted_artifact(
            base=base, published_path=Path(relative)
        )
        rows.append(
            f"| {label} | `{manifest['run_id']}` | "
            f"`{manifest['evidence_category']}` | "
            f"`{manifest['seeds']}` | `{manifest['report']['sha256']}` |"
        )
    return "\n".join([
        START,
        "| Evidence set | Promoted run | Category | Seeds | Report SHA-256 |",
        "|---|---|---|---|---|",
        *rows,
        END,
    ])


def update_readme(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("README must contain exactly one promoted-evidence block")
    before, remainder = text.split(START, 1)
    _old, after = remainder.split(END, 1)
    updated = before + block + after
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", type=Path, default=BASE / "README.md")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="Promoted result to verify; defaults to all canonical studies",
    )
    args = parser.parse_args()
    artifacts = []
    for raw in args.artifact:
        if "=" not in raw:
            parser.error("--artifact must be LABEL=PATH")
        artifacts.append(tuple(raw.split("=", 1)))
    artifacts = artifacts or list(DEFAULT_ARTIFACTS)
    update_readme(args.readme, render_block(BASE, artifacts))


if __name__ == "__main__":
    main()
