#!/usr/bin/env python3
"""Test-only Ethernet probe from a Mininet drone namespace.

The source MAC intentionally differs from the registry identity so the ingress
switch table-miss rule sends a real PacketIn to Ryu. Never use this as sensor
or production evidence.
"""

from __future__ import annotations

import argparse
import socket
import time


def mac_bytes(value: str) -> bytes:
    parts = value.split(":")
    if len(parts) != 6:
        raise ValueError("MAC must have six octets")
    return bytes(int(part, 16) for part in parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--source-mac", required=True)
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("count must be between 1 and 100")
    frame = (
        mac_bytes("00:00:00:00:00:01")
        + mac_bytes(args.source_mac)
        + bytes.fromhex("88b5")
        + b"flare-packet-in-test".ljust(46, b"\x00")
    )
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as channel:
        channel.bind((args.interface, 0))
        for _ in range(args.count):
            channel.send(frame)
            time.sleep(0.02)


if __name__ == "__main__":
    main()
