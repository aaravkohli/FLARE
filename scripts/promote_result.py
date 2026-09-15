#!/usr/bin/env python3
"""Promote a verified immutable FLARE experiment run."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from provenance import DEFAULT_REQUIRED_SEEDS, promote_run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--published", required=True, type=Path)
    parser.add_argument(
        "--required-seeds", type=int, nargs="+", default=list(DEFAULT_REQUIRED_SEEDS)
    )
    args = parser.parse_args()
    destination = promote_run(
        base=BASE,
        run_dir=args.run_dir,
        published_path=args.published,
        expected_experiment=args.experiment,
        expected_protocol=args.protocol,
        required_seeds=args.required_seeds,
    )
    print(f"Promoted verified result to {destination}")


if __name__ == "__main__":
    main()
