#!/usr/bin/env python3
"""Run an isolated Flower round and live fleet-manager reconciliation gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import yaml


BASE = Path(__file__).resolve().parent.parent
FIXTURE = BASE / "tests" / "fixtures" / "fleet_registry.yaml"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until(predicate, *, timeout_s: float, message: str):
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.1)
    detail = f": {last_error}" if last_error is not None else ""
    raise TimeoutError(message + detail)


def _port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _manager_clients(status_path: Path, expected: set[str]) -> dict | None:
    if not status_path.is_file():
        return None
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    clients = payload.get("clients", {})
    if expected.issubset(clients) and all(
        clients[client_id].get("status") == "active" for client_id in expected
    ):
        return payload
    return None


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(*, timeout_s: float = 60.0, rounds: int = 2) -> dict:
    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    fixture_drones = fixture["drones"]
    initial_ids = list(fixture_drones)[:2]
    if len(initial_ids) != 2:
        raise RuntimeError("the isolated smoke fixture must contain at least two drones")
    dynamic_id = "smoke_dynamic"
    registry = {
        "version": 1,
        "drones": {client_id: fixture_drones[client_id] for client_id in initial_ids},
    }
    port = _free_port()
    server: subprocess.Popen | None = None
    manager: subprocess.Popen | None = None
    with tempfile.TemporaryDirectory(prefix="flare-fl-smoke-") as temporary_name:
        temporary = Path(temporary_name)
        registry_path = temporary / "fleet_registry.yaml"
        status_path = temporary / "fl_clients.json"
        candidate_path = temporary / "fl_candidate.pth"
        server_log_path = temporary / "server.log"
        manager_log_path = temporary / "manager.log"
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        environment = {
            **os.environ,
            "PYTHONPATH": str(BASE),
            "FLARE_FLEET_REGISTRY": str(registry_path),
            "FLARE_FL_METRICS_JSON": str(temporary / "fl_metrics_snapshot.json"),
            "FLARE_FL_METRICS_CSV": str(temporary / "fl_advanced_metrics.csv"),
            "FLARE_FL_LEGACY_METRICS_CSV": str(temporary / "fl_round_metrics.csv"),
            "FLARE_FL_TRUST_STATE": str(temporary / "fl_trust_state.json"),
        }
        with server_log_path.open("w", encoding="utf-8") as server_log, manager_log_path.open(
            "w", encoding="utf-8"
        ) as manager_log:
            try:
                server = subprocess.Popen(
                    [
                        sys.executable, "-m", "fl.server", "--rounds", str(rounds),
                        "--server-address", f"127.0.0.1:{port}",
                        "--output", str(candidate_path),
                    ],
                    cwd=BASE,
                    env=environment,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_until(
                    lambda: _port_ready(port), timeout_s=timeout_s / 3,
                    message="Flower server did not become ready",
                )
                manager = subprocess.Popen(
                    [
                        sys.executable, "-m", "fl.client_manager",
                        "--server-address", f"127.0.0.1:{port}",
                        "--status-path", str(status_path),
                        "--reconcile-interval", "0.2",
                    ],
                    cwd=BASE,
                    env=environment,
                    stdout=manager_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_until(
                    lambda: _manager_clients(status_path, set(initial_ids)),
                    timeout_s=timeout_s / 3,
                    message="the two initial Flower clients did not become active",
                )
                registry["drones"][dynamic_id] = {
                    "display_name": "Smoke Dynamic",
                    "index": 3,
                    "mac": "02:00:00:00:10:03",
                    "access_port": 6,
                    "enabled": True,
                    "rf_profile": {
                        "rssi_offset": 0.0,
                        "pdr_offset": 0.0,
                        "latency_factor": 1.0,
                    },
                }
                replacement = registry_path.with_suffix(".tmp")
                replacement.write_text(
                    yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
                )
                os.replace(replacement, registry_path)
                status = _wait_until(
                    lambda: _manager_clients(
                        status_path, {*initial_ids, dynamic_id}
                    ),
                    timeout_s=timeout_s / 3,
                    message="fleet manager did not reconcile the enrolled client",
                )
                server_exit = server.wait(timeout=timeout_s)
                if server_exit != 0:
                    raise RuntimeError(
                        f"Flower server exited {server_exit}; log:\n"
                        + server_log_path.read_text(encoding="utf-8")[-4000:]
                    )
                metadata_path = candidate_path.with_name(
                    f"{candidate_path.name}.metadata.json"
                )
                if not candidate_path.is_file() or not metadata_path.is_file():
                    raise RuntimeError("Flower smoke run did not produce a candidate and sidecar")
                return {
                    "passed": True,
                    "rounds": rounds,
                    "initial_clients": initial_ids,
                    "dynamically_enrolled_client": dynamic_id,
                    "reconciled_clients": sorted(status["clients"]),
                    "candidate_bytes": candidate_path.stat().st_size,
                    "operator_registry_modified": False,
                    "deployed_checkpoint_modified": False,
                    "evidence_category": "controlled_simulation",
                }
            finally:
                _terminate(manager)
                _terminate(server)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    if args.timeout <= 0 or args.rounds < 1:
        parser.error("timeout and rounds must be positive")
    print(json.dumps(run(timeout_s=args.timeout, rounds=args.rounds), indent=2))


if __name__ == "__main__":
    main()
