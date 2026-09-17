#!/usr/bin/env bash
# =============================================================================
# scripts/start.sh — One-Command Startup for FLARE (macOS / Linux)
# =============================================================================

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
cd "$REPO_ROOT"

# Load .env if present
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env 2>/dev/null || true
    set +a
fi

WITH_FRONTEND=1
for arg in "$@"; do
    case "$arg" in
        --no-frontend|--headless)
            WITH_FRONTEND=0
            ;;
        --help|-h)
            echo "Usage: $0 [--no-frontend | --headless]"
            echo "Starts the FLARE simulation backend stack and frontend dashboard."
            exit 0
            ;;
    esac
done

if [ ! -x "venv/bin/python" ]; then
    printf '%b\n' "${RED}[ERROR] Virtual environment not found at venv/bin/python.${NC}"
    printf '%b\n' "Please run the setup script first: ${CYAN}./scripts/setup.sh${NC}\n"
    exit 1
fi

# Run backend simulation
"$REPO_ROOT/run_local_simulation.sh"

# Optionally start frontend
if [ "$WITH_FRONTEND" -eq 1 ] && command -v npm >/dev/null 2>&1 && [ -d "frontend-react" ]; then
    FRONTEND_PORT="${FRONTEND_PORT:-5173}"
    if lsof -nP -tiTCP:"$FRONTEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        printf '%b\n' "${GREEN}[INFO] Frontend is already running on port ${FRONTEND_PORT}.${NC}"
    else
        printf '%b\n' "${CYAN}[STARTING] Launching Frontend dev server on port ${FRONTEND_PORT}...${NC}"
        (cd frontend-react && npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT") > logs/frontend.log 2>&1 &
        FRONTEND_PID=$!
        printf 'frontend:%s:npm run dev\n' "$FRONTEND_PID" >> "$REPO_ROOT/.local_sim.pids"
        sleep 1
        printf '%b\n' "${GREEN}[READY] Frontend dev server started (PID ${FRONTEND_PID}) -> logs/frontend.log${NC}"
    fi
fi

printf '\n%b\n' "${GREEN}${BOLD}All services active. Dashboard available at: http://localhost:${FRONTEND_PORT:-5173}/${NC}"
printf 'Diagnostic check: %bpython scripts/healthcheck.py%b\n' "${CYAN}" "${NC}"
printf 'To stop: %b./stop_local_simulation.sh%b\n\n' "${YELLOW}" "${NC}"
