#!/usr/bin/env bash
# Start or restart FLARE's lightweight host-native simulation.

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR" || exit 1

PID_FILE="${SCRIPT_DIR}/.local_sim.pids"
STARTUP_COMPLETE=0

printf '%b\n' "$CYAN"
echo "=========================================================="
echo "       FLARE CROSS-LAYER UAV SIMULATION"
echo "        Lightweight Host-Native Deployment"
echo "=========================================================="
printf '%b\n' "$NC"

if [ ! -x "venv/bin/python" ]; then
    printf '%b\n' "${RED}[ERROR] Executable virtual environment not found at venv/bin/python.${NC}"
    echo "Create it with: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

if ! command -v lsof >/dev/null 2>&1; then
    printf '%b\n' "${RED}[ERROR] lsof is required for safe port ownership checks.${NC}"
    exit 1
fi

mkdir -p logs models experiments

cleanup_failed_startup() {
    local status=$?
    if [ "$STARTUP_COMPLETE" -eq 0 ] && [ -s "$PID_FILE" ]; then
        printf '%b\n' "${YELLOW}[INFO] Cleaning up the partial FLARE startup...${NC}"
        "$SCRIPT_DIR/stop_local_simulation.sh" --quiet || true
    fi
    exit "$status"
}
trap cleanup_failed_startup EXIT HUP INT TERM

listener_pids() {
    lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

port_is_listening_for_pid() {
    local port="$1"
    local expected_pid="$2"
    local pid
    for pid in $(listener_pids "$port"); do
        [ "$pid" = "$expected_pid" ] && return 0
    done
    return 1
}

show_port_owner() {
    local port="$1"
    local pid
    for pid in $(listener_pids "$port"); do
        ps -p "$pid" -o pid=,command= 2>/dev/null || echo "PID ${pid}"
    done
}

printf '%b\n' "${BLUE}[INFO] Removing stale FLARE-owned processes from previous runs...${NC}"
"$SCRIPT_DIR/stop_local_simulation.sh" --quiet

for port in 8000 8080 8090; do
    if [ -n "$(listener_pids "$port")" ]; then
        printf '%b\n' "${RED}[ERROR] Port ${port} is owned by a process outside this FLARE run:${NC}"
        show_port_owner "$port"
        echo "FLARE will not terminate an unrelated process. Stop it explicitly or change its port."
        exit 1
    fi
done

printf '%b\n' "${BLUE}[INFO] Configuring execution mode as simulation...${NC}"
if [ -f "config/mode.yaml" ]; then
    sed -i '' 's/mode: real/mode: simulation/g' config/mode.yaml 2>/dev/null \
        || sed -i 's/mode: real/mode: simulation/g' config/mode.yaml
fi

# A prior research attack must never silently carry into a normal demo run.
# Experiments that deliberately need persisted attack state can opt in with
# FLARE_PRESERVE_BYZANTINE_STATE=1.
if [ "${FLARE_PRESERVE_BYZANTINE_STATE:-0}" != "1" ]; then
    printf '%b\n' "${BLUE}[INFO] Restoring all federated clients to protected normal mode...${NC}"
    venv/bin/python -m simulation.byzantine_state --reset || exit 1
fi

: > "$PID_FILE"

launch_service() {
    local name="$1"
    local log="$2"
    local marker="$3"
    local port="$4"
    shift 4

    printf '%b\n' "${YELLOW}[STARTING] ${name}...${NC}"
    "$@" > "$log" 2>&1 &
    local pid=$!
    printf '%s:%s:%s\n' "$name" "$pid" "$marker" >> "$PID_FILE"

    local attempt
    if [ "$port" != "-" ]; then
        for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                break
            fi
            if port_is_listening_for_pid "$port" "$pid"; then
                printf '%b\n' "${GREEN}[READY] ${name} (PID ${pid}, port ${port}) -> ${log}${NC}"
                return 0
            fi
            sleep 0.3
        done
        printf '%b\n' "${RED}[FAILED] ${name} did not become ready on port ${port}. See ${log}.${NC}"
    else
        sleep 0.8
        if kill -0 "$pid" >/dev/null 2>&1; then
            printf '%b\n' "${GREEN}[RUNNING] ${name} (PID ${pid}) -> ${log}${NC}"
            return 0
        fi
        printf '%b\n' "${RED}[FAILED] ${name} exited during startup. See ${log}.${NC}"
    fi

    tail -n 12 "$log" 2>/dev/null || true
    return 1
}

launch_service "FL-Server" "logs/fl_server.log" "fl/server.py" 8090 \
    venv/bin/python fl/server.py --rounds 20 || exit 1

while IFS= read -r drone_id; do
    [ -n "$drone_id" ] || continue
    client_args=(venv/bin/python fl/client.py --client_id "$drone_id" --server_address localhost:8090)
    # Preserve the original heterogeneous local-data demonstration for drone_3.
    [ "$drone_id" = "drone_3" ] && client_args+=(--jammed)
    launch_service "FL-Client-${drone_id}" "logs/fl_client_${drone_id}.log" "fl/client.py --client_id ${drone_id}" - \
        "${client_args[@]}" || exit 1
done < <(venv/bin/python -m fleet.cli list-ids)

launch_service "Mock-SDN" "logs/mock_sdn.log" "sdn/mock_sdn.py" 8080 \
    venv/bin/python sdn/mock_sdn.py || exit 1
launch_service "API-Server" "logs/api_server.log" "uvicorn api.server:app" 8000 \
    venv/bin/python -m uvicorn api.server:app --host 127.0.0.1 --port 8000 || exit 1
launch_service "Orchestrator-Loop" "logs/orchestrator.log" "orchestrator.loop" - \
    venv/bin/python -m orchestrator.loop || exit 1

STARTUP_COMPLETE=1
trap - EXIT HUP INT TERM

printf '\n%b\n' "${GREEN}==========================================================${NC}"
printf '%b\n' "${GREEN}       FLARE LOCAL SERVICES ARE READY${NC}"
printf '%b\n' "${GREEN}==========================================================${NC}"
printf 'Dashboard: %bhttp://localhost:5173/%b (start it with: cd frontend-react && npm run dev)\n' "$CYAN" "$NC"
printf 'Stop or restart safely with: %b./stop_local_simulation.sh%b / %b./run_local_simulation.sh%b\n\n' "$MAGENTA" "$NC" "$MAGENTA" "$NC"
