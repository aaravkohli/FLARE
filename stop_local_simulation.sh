#!/usr/bin/env bash
# =============================================================================
# stop_local_simulation.sh
# Anti-Jamming Drone System - Local Simulation Shutdown Script
# Terminates all host-native simulation services cleanly.
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}[INFO] Stopping all local simulation services...${NC}"

PID_FILE=".local_sim.pids"

if [ ! -f "$PID_FILE" ]; then
    echo -e "${YELLOW}[WARNING] No active local simulation PIDs file found (${PID_FILE}).${NC}"
    echo "Attempting generic cleanup for Capstone Python processes..."
    
    # Generic cleanup by finding process names if pid file is missing
    pkill -f "fl/server.py"
    pkill -f "fl/client.py"
    pkill -f "sdn/mock_sdn.py"
    pkill -f "orchestrator.loop"
    pkill -f "uvicorn api.server:app"
    
    echo -e "${GREEN}[CLEANUP DONE] Generic cleanup completed.${NC}"
    exit 0
fi

# Stop each process in reverse order of startup
if command -v tac >/dev/null 2>&1; then
    REVERSE_CMD="tac"
else
    REVERSE_CMD="tail -r"
fi

$REVERSE_CMD "$PID_FILE" | while IFS=: read -r name pid; do
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
        echo -e "${YELLOW}[STOPPING] Killing ${name} (PID: ${pid})...${NC}"
        kill "$pid"
        
        # Wait up to 3 seconds for graceful shutdown
        for i in {1..6}; do
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                break
            fi
            sleep 0.5
        done
        
        # Force kill if still running
        if kill -0 "$pid" >/dev/null 2>&1; then
            echo -e "${RED}[WARNING] Process ${name} (PID: ${pid}) did not exit. Force killing...${NC}"
            kill -9 "$pid"
        fi
    else
        echo -e "${NC}Service ${name} (PID: ${pid}) was already stopped."
    fi
done

rm -f "$PID_FILE"
echo -e "${GREEN}[SUCCESS] All local simulation services stopped successfully.${NC}"
