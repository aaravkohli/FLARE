"""Fleet-registry-aware Flower client process supervisor."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable

import yaml

from fleet.registry import active_drone_ids


BASE = Path(__file__).resolve().parent.parent
FL_CONFIG = yaml.safe_load((BASE / "config" / "fl_config.yaml").read_text())


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class FleetClientManager:
    def __init__(
        self,
        *,
        server_address: str,
        real: bool,
        require_real_data: bool,
        processed_dir: Path,
        status_path: Path,
        reconcile_interval_s: float,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.server_address = server_address
        self.real = real
        self.require_real_data = require_real_data
        self.processed_dir = processed_dir
        self.status_path = status_path
        self.reconcile_interval_s = max(0.2, float(reconcile_interval_s))
        self._popen = popen
        self._processes: dict[str, subprocess.Popen] = {}
        self._stopping = False
        manager_cfg = FL_CONFIG.get("client_manager", {})
        self._jammed_clients = set(manager_cfg.get("simulation_jammed_clients", []))

    def dataset_path(self, client_id: str) -> Path:
        return self.processed_dir / f"{client_id}_train.csv"

    def command_for(self, client_id: str) -> list[str]:
        command = [
            sys.executable,
            str(BASE / "fl" / "client.py"),
            "--client_id", client_id,
            "--server_address", self.server_address,
        ]
        if self.real:
            command.append("--real")
        if self.require_real_data:
            command.append("--require-real-data")
        if not self.real and client_id in self._jammed_clients:
            command.append("--jammed")
        return command

    def reconcile_once(self) -> dict:
        active = set(active_drone_ids())
        for client_id in list(self._processes):
            process = self._processes[client_id]
            if client_id not in active or process.poll() is not None:
                if process.poll() is None:
                    process.terminate()
                self._processes.pop(client_id, None)

        clients: dict[str, dict] = {}
        for client_id in sorted(active):
            dataset = self.dataset_path(client_id)
            if self.real and self.require_real_data and not dataset.is_file():
                clients[client_id] = {
                    "status": "real_data_unavailable",
                    "dataset": str(dataset),
                    "pid": None,
                }
                continue
            process = self._processes.get(client_id)
            if process is None:
                command = self.command_for(client_id)
                process = self._popen(command, cwd=BASE)
                self._processes[client_id] = process
            clients[client_id] = {
                "status": "active" if process.poll() is None else "exited",
                "dataset": str(dataset) if self.real else None,
                "pid": process.pid,
                "command": self.command_for(client_id),
            }
        status = {
            "schema_version": "fl_client_manager_status_v1",
            "mode": "real" if self.real else "simulation",
            "server_address": self.server_address,
            "updated_at": time.time(),
            "clients": clients,
        }
        _atomic_json(self.status_path, status)
        return status

    def stop(self) -> None:
        self._stopping = True
        for process in self._processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 5.0
        for process in self._processes.values():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
        self._processes.clear()

    def run(self) -> None:
        while not self._stopping:
            self.reconcile_once()
            time.sleep(self.reconcile_interval_s)


def main() -> None:
    config = FL_CONFIG.get("client_manager", {})
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-address", default="127.0.0.1:8090")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--require-real-data", action="store_true")
    parser.add_argument(
        "--processed-dir", type=Path, default=BASE / "datasets" / "processed"
    )
    parser.add_argument(
        "--status-path", type=Path,
        default=Path(os.getenv("FLARE_CLIENT_MANAGER_STATUS", BASE / "runtime" / "fl_clients.json")),
    )
    parser.add_argument(
        "--reconcile-interval", type=float,
        default=float(config.get("reconcile_interval_s", 2.0)),
    )
    args = parser.parse_args()
    if args.require_real_data and not args.real:
        parser.error("--require-real-data requires --real")
    manager = FleetClientManager(
        server_address=args.server_address,
        real=args.real,
        require_real_data=args.require_real_data,
        processed_dir=args.processed_dir,
        status_path=args.status_path,
        reconcile_interval_s=args.reconcile_interval,
    )
    signal.signal(signal.SIGTERM, lambda *_args: manager.stop())
    signal.signal(signal.SIGINT, lambda *_args: manager.stop())
    try:
        manager.run()
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
