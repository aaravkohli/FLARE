#!/usr/bin/env python3
"""
scripts/healthcheck.py — FLARE Diagnostic Health Check

Performs non-destructive diagnostics on running FLARE services:
  - API Server (/health and /ready)
  - Mock/Real SDN Controller (/health and /ready)
  - FL Server (TCP socket connectivity)
  - Frontend dev/preview server (HTTP check)
  - Configuration & Model file integrity

Usage:
  python scripts/healthcheck.py [--api-url http://127.0.0.1:8000]
                                [--sdn-url http://127.0.0.1:8080]
                                [--fl-host 127.0.0.1] [--fl-port 8090]
                                [--frontend-url http://localhost:5173]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, dict | str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FLARE-Healthcheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(data)
            except json.JSONDecodeError:
                return resp.status, data
    except urllib.error.HTTPError as exc:
        data = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(data)
        except Exception:
            return exc.code, data
    except Exception as exc:
        return 0, str(exc)


def _tcp_check(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_local_files() -> list[str]:
    issues = []
    required_configs = [
        "config/fl_config.yaml",
        "config/fleet_registry.yaml",
        "config/mode.yaml",
        "config/rl_config.yaml",
        "config/sdn_config.yaml",
        "config/security_config.yaml",
    ]
    for cfg in required_configs:
        path = REPO_ROOT / cfg
        if not path.is_file():
            issues.append(f"Missing config file: {cfg}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="FLARE System Diagnostic Health Check")
    parser.add_argument(
        "--api-url",
        default=os.getenv("API_URL", f"http://127.0.0.1:{os.getenv('API_PORT', '8000')}"),
        help="Base URL for FLARE API server",
    )
    parser.add_argument(
        "--sdn-url",
        default=os.getenv("SDN_URL", f"http://127.0.0.1:{os.getenv('SDN_PORT', '8080')}"),
        help="Base URL for SDN controller",
    )
    parser.add_argument(
        "--fl-host",
        default=os.getenv("FL_SERVER_HOST", "127.0.0.1"),
        help="FL server host",
    )
    parser.add_argument(
        "--fl-port",
        type=int,
        default=int(os.getenv("FL_SERVER_PORT", "8090")),
        help="FL server port",
    )
    parser.add_argument(
        "--frontend-url",
        default=f"http://localhost:{os.getenv('FRONTEND_PORT', '5173')}",
        help="Frontend URL",
    )
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}=== FLARE System Health Diagnostics ==={RESET}\n")

    overall_ok = True

    # 1. Configuration check
    print(f"{BOLD}[1/5] Repository Configuration & Integrity{RESET}")
    file_issues = check_local_files()
    if file_issues:
        for issue in file_issues:
            print(f"  {RED}✖ {issue}{RESET}")
        overall_ok = False
    else:
        print(f"  {GREEN}✔ Required YAML configuration files present.{RESET}")

    # 2. Mock / Real SDN Controller check
    print(f"\n{BOLD}[2/5] SDN Controller ({args.sdn_url}){RESET}")
    sdn_code, sdn_data = _http_get(f"{args.sdn_url.rstrip('/')}/health")
    if sdn_code == 200:
        mode = sdn_data.get("mode", "unknown") if isinstance(sdn_data, dict) else "unknown"
        print(f"  {GREEN}✔ Controller liveness OK (status 200, mode: {mode}){RESET}")
        ready_code, ready_data = _http_get(f"{args.sdn_url.rstrip('/')}/ready")
        if ready_code == 200:
            print(f"  {GREEN}✔ Controller data plane ready.{RESET}")
        else:
            print(f"  {YELLOW}⚠ Controller readiness returned {ready_code}: {ready_data}{RESET}")
    else:
        print(f"  {RED}✖ SDN controller unreachable at {args.sdn_url} ({sdn_data}){RESET}")
        print(f"    {YELLOW}Tip: Start it with `python sdn/mock_sdn.py` or `./scripts/start.sh`{RESET}")
        overall_ok = False

    # 3. Federated Learning Server check
    print(f"\n{BOLD}[3/5] FL Server Socket ({args.fl_host}:{args.fl_port}){RESET}")
    fl_up = _tcp_check(args.fl_host, args.fl_port)
    if fl_up:
        print(f"  {GREEN}✔ FL server port {args.fl_port} is open and accepting TCP connections.{RESET}")
    else:
        print(f"  {RED}✖ FL server not accepting connections on {args.fl_host}:{args.fl_port}.{RESET}")
        print(f"    {YELLOW}Tip: Start it with `python fl/server.py --rounds 20`{RESET}")
        overall_ok = False

    # 4. API Server check
    print(f"\n{BOLD}[4/5] API Server ({args.api_url}){RESET}")
    api_code, api_data = _http_get(f"{args.api_url.rstrip('/')}/health")
    if api_code == 200 and isinstance(api_data, dict):
        mode = api_data.get("mode", "unknown")
        fl_loaded = api_data.get("fl_loaded", False)
        rl_loaded = api_data.get("rl_loaded", False)
        uptime = api_data.get("uptime_s", 0)
        print(f"  {GREEN}✔ API server online (uptime: {uptime}s, mode: {mode}){RESET}")
        print(f"    - Threat BiLSTM loaded: {GREEN if fl_loaded else YELLOW}{fl_loaded}{RESET}")
        print(f"    - Routing RL agent loaded: {GREEN if rl_loaded else YELLOW}{rl_loaded}{RESET}")

        ready_code, ready_data = _http_get(f"{args.api_url.rstrip('/')}/ready")
        if ready_code == 200:
            print(f"  {GREEN}✔ System readiness: READY{RESET}")
        else:
            print(f"  {YELLOW}⚠ System readiness: 503 Service Unavailable (SDN data-plane not fully connected yet){RESET}")
    else:
        print(f"  {RED}✖ API server unreachable at {args.api_url} ({api_data}){RESET}")
        print(f"    {YELLOW}Tip: Start it with `python -m uvicorn api.server:app --port 8000`{RESET}")
        overall_ok = False

    # 5. Frontend check (optional/informational)
    print(f"\n{BOLD}[5/5] Frontend Dashboard ({args.frontend_url}){RESET}")
    front_code, _ = _http_get(args.frontend_url, timeout=1.5)
    if front_code in {200, 304}:
        print(f"  {GREEN}✔ Frontend UI is responding on {args.frontend_url}{RESET}")
    else:
        print(f"  {YELLOW}ℹ Frontend not responding on {args.frontend_url} (optional for headless/API usage){RESET}")
        print(f"    {YELLOW}Tip: Run `cd frontend-react && npm run dev` to launch UI{RESET}")

    print("\n" + "=" * 55)
    if overall_ok:
        print(f"{BOLD}{GREEN}ALL CORE BACKEND SERVICES ARE HEALTHY AND RUNNING{RESET}")
        print("=" * 55 + "\n")
        return 0
    else:
        print(f"{BOLD}{RED}ONE OR MORE CORE SERVICES ARE NOT READY{RESET}")
        print("=" * 55 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
