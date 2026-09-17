#!/usr/bin/env python3
"""
scripts/start.py — Cross-Platform FLARE Orchestrator & Process Runner

Works identically on macOS, Linux, and Windows:
  - Detects Python virtual environment (venv/bin/python or venv\\Scripts\\python.exe)
  - Validates port availability without system-specific tools (lsof/netstat)
  - Resets simulation state if needed
  - Spawns all FLARE services with dedicated log files
  - Monitors processes and cleanly shuts down on Ctrl+C / SIGINT

Usage:
  python scripts/start.py [--no-frontend] [--check]
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Terminal Colors (ANSI enabled on modern Windows terminals, macOS, Linux)
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def find_venv_python() -> str:
    """Locate the Python executable in the virtual environment."""
    if sys.platform == "win32":
        candidate = REPO_ROOT / "venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO_ROOT / "venv" / "bin" / "python"

    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    # Fallback to current sys.executable
    return sys.executable


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def load_env_file() -> None:
    """Load simple KEY=VALUE pairs from .env if present."""
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


class ProcessManager:
    def __init__(self) -> None:
        self.processes: List[Tuple[str, subprocess.Popen, Path]] = []
        self._shutting_down = False

    def launch(self, name: str, cmd: List[str], log_path: Path) -> subprocess.Popen:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=log_file,
            stderr=subprocess.STLOG if hasattr(subprocess, "STLOG") else log_file,
            env=os.environ.copy(),
        )
        self.processes.append((name, proc, log_path))
        return proc

    def stop_all(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        print(f"\n{YELLOW}[SHUTDOWN] Stopping all active FLARE services...{RESET}")

        for name, proc, _ in reversed(self.processes):
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass

        # Wait up to 3 seconds for graceful shutdown
        end_time = time.time() + 3.0
        for name, proc, _ in self.processes:
            while proc.poll() is None and time.time() < end_time:
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass

        print(f"{GREEN}[SHUTDOWN] All services stopped cleanly.{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="FLARE Cross-Platform Launcher")
    parser.add_argument("--no-frontend", action="store_true", help="Do not start frontend dev server")
    parser.add_argument("--check", action="store_true", help="Only verify configuration and ports, do not launch")
    args = parser.parse_args()

    load_env_file()

    api_port = int(os.getenv("API_PORT", "8000"))
    sdn_port = int(os.getenv("SDN_PORT", "8080"))
    fl_port = int(os.getenv("FL_SERVER_PORT", "8090"))
    frontend_port = int(os.getenv("FRONTEND_PORT", "5173"))
    api_host = os.getenv("API_HOST", "0.0.0.0")

    print(f"\n{BOLD}{CYAN}=========================================================={RESET}")
    print(f"{BOLD}{CYAN}       FLARE CROSS-PLATFORM SYSTEM LAUNCHER{RESET}")
    print(f"{BOLD}{CYAN}=========================================================={RESET}\n")

    python_bin = find_venv_python()
    print(f"Using Python: {BOLD}{python_bin}{RESET}")

    # Check ports
    ports_to_check = [("API Server", api_port), ("Mock SDN", sdn_port), ("FL Server", fl_port)]
    conflicts = []
    for s_name, port in ports_to_check:
        if is_port_in_use(port):
            conflicts.append((s_name, port))

    if conflicts:
        for s_name, port in conflicts:
            print(f"{RED}[ERROR] Port {port} ({s_name}) is already in use by an existing process.{RESET}")
        print(f"{YELLOW}Stop existing processes or adjust ports in .env before starting.{RESET}\n")
        return 1

    if args.check:
        print(f"\n{GREEN}✔ Port and environment preflight checks PASSED.{RESET}\n")
        return 0

    # Ensure logs/ directory exists
    logs_dir = REPO_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Reset Byzantine state for demo run
    if os.getenv("FLARE_PRESERVE_BYZANTINE_STATE", "0") != "1":
        print(f"{CYAN}[INFO] Restoring federated clients to normal mode...{RESET}")
        try:
            subprocess.run(
                [python_bin, "-m", "simulation.byzantine_state", "--reset"],
                cwd=str(REPO_ROOT),
                check=True,
                capture_output=True,
            )
        except Exception as e:
            print(f"{YELLOW}[WARN] Byzantine reset warning: {e}{RESET}")

    manager = ProcessManager()

    def signal_handler(signum, frame):
        manager.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"{YELLOW}[STARTING] Launching backend services...{RESET}")

    # 1. FL Server
    manager.launch(
        "FL-Server",
        [python_bin, "fl/server.py", "--rounds", "20"],
        logs_dir / "fl_server.log",
    )
    time.sleep(1.0)

    # 2. FL Clients (drone_1 .. drone_5)
    drones = ["drone_1", "drone_2", "drone_3", "drone_4", "drone_5"]
    for d_id in drones:
        c_args = [python_bin, "fl/client.py", "--client_id", d_id, "--server_address", f"127.0.0.1:{fl_port}"]
        if d_id == "drone_3":
            c_args.append("--jammed")
        manager.launch(f"FL-Client-{d_id}", c_args, logs_dir / f"fl_client_{d_id}.log")

    # 3. Mock SDN
    manager.launch(
        "Mock-SDN",
        [python_bin, "sdn/mock_sdn.py"],
        logs_dir / "mock_sdn.log",
    )
    time.sleep(1.0)

    # 4. API Server
    manager.launch(
        "API-Server",
        [python_bin, "-m", "uvicorn", "api.server:app", "--host", api_host, "--port", str(api_port)],
        logs_dir / "api_server.log",
    )
    time.sleep(1.2)

    # 5. Orchestrator Loop
    manager.launch(
        "Orchestrator-Loop",
        [python_bin, "-m", "orchestrator.loop"],
        logs_dir / "orchestrator.log",
    )

    # 6. Frontend Dev Server
    if not args.no_frontend:
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        if not is_port_in_use(frontend_port):
            try:
                manager.launch(
                    "Frontend",
                    [npm_cmd, "run", "dev", "--", "--host", "0.0.0.0", "--port", str(frontend_port)],
                    logs_dir / "frontend.log",
                )
                print(f"{GREEN}[READY] Frontend started on http://localhost:{frontend_port}/{RESET}")
            except Exception as exc:
                print(f"{YELLOW}[WARN] Could not start npm dev server: {exc}{RESET}")
        else:
            print(f"{GREEN}[INFO] Frontend already active on port {frontend_port}{RESET}")

    print(f"\n{BOLD}{GREEN}=========================================================={RESET}")
    print(f"{BOLD}{GREEN}       FLARE SERVICES ARE RUNNING{RESET}")
    print(f"{BOLD}{GREEN}=========================================================={RESET}")
    print(f"API Server:  http://localhost:{api_port}/")
    print(f"SDN Health:  http://localhost:{sdn_port}/health")
    print(f"Dashboard:   http://localhost:{frontend_port}/")
    print(f"\n{CYAN}Press Ctrl+C to stop all services cleanly.{RESET}\n")

    try:
        while True:
            # Check if any crucial process died
            for name, proc, log_path in manager.processes:
                if proc.poll() is not None:
                    print(f"{YELLOW}[WARN] Service {name} exited with code {proc.returncode}. See {log_path}{RESET}")
            time.sleep(2.0)
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop_all()

    return 0


if __name__ == "__main__":
    sys.exit(main())
