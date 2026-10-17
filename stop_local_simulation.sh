#!/usr/bin/env bash
# Stop FLARE's host-native simulation without killing unrelated processes.

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PID_FILE="${SCRIPT_DIR}/.local_sim.pids"
QUIET=0

if [ "${1:-}" = "--quiet" ]; then
    QUIET=1
elif [ $# -gt 0 ]; then
    echo "Usage: $0 [--quiet]" >&2
    exit 2
fi

if [ "$QUIET" -eq 0 ]; then
    printf '%b\n' "${CYAN}[INFO] Stopping FLARE local simulation services...${NC}"
fi

process_command() {
    ps -p "$1" -o command= 2>/dev/null || true
}

process_cwd() {
    local pid="$1"
    if [ -e "/proc/${pid}/cwd" ]; then
        (cd "/proc/${pid}/cwd" 2>/dev/null && pwd -P) || true
        return
    fi
    lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

process_has_flare_log() {
    local pid="$1"
    local marker="$2"
    local expected_log
    case "$marker" in
        "fl/server.py") expected_log="${SCRIPT_DIR}/logs/fl_server.log" ;;
        "fl/client.py"*) expected_log="${SCRIPT_DIR}/logs/fl_client_" ;;
        "sdn/mock_sdn.py") expected_log="${SCRIPT_DIR}/logs/mock_sdn.log" ;;
        "uvicorn api.server:app") expected_log="${SCRIPT_DIR}/logs/api_server.log" ;;
        "orchestrator.loop") expected_log="${SCRIPT_DIR}/logs/orchestrator.log" ;;
        *) return 1 ;;
    esac
    lsof -p "$pid" 2>/dev/null | grep -F "$expected_log" >/dev/null 2>&1
}

expected_marker() {
    case "$1" in
        FL-Server) echo "fl/server.py" ;;
        FL-Client-*) echo "fl/client.py" ;;
        Mock-SDN) echo "sdn/mock_sdn.py" ;;
        API-Server) echo "uvicorn api.server:app" ;;
        Orchestrator-Loop) echo "orchestrator.loop" ;;
        *) echo "" ;;
    esac
}

is_project_process() {
    local pid="$1"
    local marker="$2"
    local command cwd
    kill -0 "$pid" >/dev/null 2>&1 || return 1
    command="$(process_command "$pid")"
    cwd="$(process_cwd "$pid")"
    # Some restricted shells deny `ps` while still allowing lsof/kill. Require
    # both the exact project cwd and a known FLARE log descriptor in that case.
    if [ -z "$command" ]; then
        [ "$cwd" = "$SCRIPT_DIR" ] && process_has_flare_log "$pid" "$marker"
        return
    fi
    case "$command" in
        *"$marker"*) ;;
        *) return 1 ;;
    esac
    [ "$cwd" = "$SCRIPT_DIR" ] || case "$command" in
        *"${SCRIPT_DIR}/"*) return 0 ;;
        *) return 1 ;;
    esac
}

STOP_NAMES=()
STOP_PIDS=()
STOP_MARKERS=()
STOP_COUNT=0

is_queued() {
    local candidate="$1"
    local index=0
    while [ "$index" -lt "$STOP_COUNT" ]; do
        [ "${STOP_PIDS[$index]}" = "$candidate" ] && return 0
        index=$((index + 1))
    done
    return 1
}

queue_pid() {
    local name="$1"
    local pid="$2"
    local marker="$3"

    if ! kill -0 "$pid" >/dev/null 2>&1; then
        [ "$QUIET" -eq 1 ] || printf '%s\n' "[INFO] ${name} (PID ${pid}) is already stopped."
        return 0
    fi
    if ! is_project_process "$pid" "$marker"; then
        printf '%b\n' "${YELLOW}[SKIP] PID ${pid} is no longer the recorded ${name}; it will not be killed.${NC}" >&2
        return 0
    fi
    is_queued "$pid" && return 0
    STOP_NAMES[$STOP_COUNT]="$name"
    STOP_PIDS[$STOP_COUNT]="$pid"
    STOP_MARKERS[$STOP_COUNT]="$marker"
    STOP_COUNT=$((STOP_COUNT + 1))
}

stop_recorded_processes() {
    [ -f "$PID_FILE" ] || return 0
    while IFS=: read -r name pid marker; do
        [ -n "${name:-}" ] || continue
        [ -n "${pid:-}" ] || continue
        case "$pid" in
            *[!0-9]*)
                printf '%b\n' "${YELLOW}[SKIP] Invalid PID entry for ${name}: ${pid}${NC}" >&2
                continue
                ;;
        esac
        if [ -z "${marker:-}" ]; then
            marker="$(expected_marker "$name")"
        fi
        [ -n "$marker" ] || continue
        queue_pid "$name" "$pid" "$marker"
    done < <(reverse_pid_file)
    rm -f -- "$PID_FILE"
}

reverse_pid_file() {
    if command -v tac >/dev/null 2>&1; then
        tac "$PID_FILE"
    else
        tail -r "$PID_FILE"
    fi
}

discover_project_processes() {
    local marker name pid
    for entry in \
        "Orchestrator-Loop|orchestrator.loop" \
        "API-Server|uvicorn api.server:app" \
        "Mock-SDN|sdn/mock_sdn.py" \
        "FL-Client|fl/client.py" \
        "FL-Server|fl/server.py"; do
        name="${entry%%|*}"
        marker="${entry#*|}"
        command -v pgrep >/dev/null 2>&1 || continue
        for pid in $(pgrep -f "$marker" 2>/dev/null || true); do
            if is_project_process "$pid" "$marker"; then
                queue_pid "$name" "$pid" "$marker"
            fi
        done
    done

    # Listener fallback also works where process-list inspection is restricted.
    # Exact cwd verification in queue_pid prevents touching unrelated services.
    for entry in "API-Server|8000|uvicorn api.server:app" \
                 "Mock-SDN|8080|sdn/mock_sdn.py" \
                 "FL-Server|8090|fl/server.py"; do
        name="${entry%%|*}"
        port_marker="${entry#*|}"
        port="${port_marker%%|*}"
        marker="${port_marker#*|}"
        for pid in $(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
            queue_pid "$name" "$pid" "$marker"
        done
    done
}

stop_queued_processes() {
    local index attempt any_running
    index=0
    while [ "$index" -lt "$STOP_COUNT" ]; do
        printf '%b\n' "${YELLOW}[STOPPING] ${STOP_NAMES[$index]} (PID ${STOP_PIDS[$index]})...${NC}"
        kill -TERM "${STOP_PIDS[$index]}" 2>/dev/null || true
        index=$((index + 1))
    done

    # One shared grace period keeps restart time bounded regardless of how many
    # services are active.
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        any_running=0
        index=0
        while [ "$index" -lt "$STOP_COUNT" ]; do
            if is_project_process "${STOP_PIDS[$index]}" "${STOP_MARKERS[$index]}"; then
                any_running=1
            fi
            index=$((index + 1))
        done
        [ "$any_running" -eq 0 ] && return 0
        sleep 0.2
    done

    index=0
    while [ "$index" -lt "$STOP_COUNT" ]; do
        if is_project_process "${STOP_PIDS[$index]}" "${STOP_MARKERS[$index]}"; then
            printf '%b\n' "${RED}[WARNING] ${STOP_NAMES[$index]} did not stop gracefully; terminating PID ${STOP_PIDS[$index]}.${NC}"
            kill -KILL "${STOP_PIDS[$index]}" 2>/dev/null || true
        fi
        index=$((index + 1))
    done
}

stop_recorded_processes
# Catch project-owned leftovers when a shell was interrupted or the PID file
# was removed. The cwd/command checks prevent global pkill behavior.
discover_project_processes
stop_queued_processes

if [ "$QUIET" -eq 0 ]; then
    printf '%b\n' "${GREEN}[SUCCESS] FLARE local simulation services are stopped.${NC}"
fi
