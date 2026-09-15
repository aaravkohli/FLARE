#!/usr/bin/env python3
"""Generate or receive test-only UDP control traffic inside Mininet hosts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import tempfile
import time


def _write_counter(path: Path, received: int, by_source: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump({
                "received_packets": received,
                "received_by_source_ip": by_source,
                "updated_at": time.time(),
            }, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("server", "client"))
    parser.add_argument("--host", default="10.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--payload", default="flare-control-probe")
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--counter-file", type=Path)
    args = parser.parse_args()
    channel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.mode == "server":
        channel.bind(("0.0.0.0", args.port))
        received = 0
        by_source: dict[str, int] = {}
        while True:
            _payload, (source_ip, _source_port) = channel.recvfrom(2048)
            received += 1
            by_source[source_ip] = by_source.get(source_ip, 0) + 1
            if args.counter_file is not None:
                _write_counter(args.counter_file, received, by_source)
    while True:
        channel.sendto(args.payload.encode("utf-8"), (args.host, args.port))
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
