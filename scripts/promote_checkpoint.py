#!/usr/bin/env python3
"""Atomically publish a validated FL or routing checkpoint generation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from runtime.model_deployment import publish_checkpoint  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=["fl", "routing"])
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--provenance-run-id", required=True)
    parser.add_argument(
        "--manifest", type=Path, default=BASE / "models" / "deployment_manifest.json"
    )
    args = parser.parse_args()
    metadata = args.metadata or args.checkpoint.with_name(
        f"{args.checkpoint.name}.metadata.json"
    )
    result = publish_checkpoint(
        base=BASE,
        manifest_path=args.manifest,
        model_name=args.model,
        checkpoint_path=args.checkpoint,
        metadata_path=metadata,
        contract=args.contract,
        provenance_run_id=args.provenance_run_id,
    )
    print(f"Published deployment generation {result['generation']}")


if __name__ == "__main__":
    main()
