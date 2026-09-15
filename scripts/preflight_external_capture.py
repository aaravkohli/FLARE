#!/usr/bin/env python3
"""Check external capture structure without claiming real-world validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from validation.external_capture import preflight_study  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        result = preflight_study(args.manifests)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"External capture preflight failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
