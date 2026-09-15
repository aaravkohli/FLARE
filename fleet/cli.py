"""Small read-only CLI used by launch scripts to discover enrolled UAVs."""

from __future__ import annotations

import argparse

from fleet.registry import list_drones


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list-ids",))
    args = parser.parse_args()
    if args.command == "list-ids":
        for drone in list_drones(enabled_only=True):
            print(drone["drone_id"])


if __name__ == "__main__":
    main()
