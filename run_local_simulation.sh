#!/usr/bin/env bash
# =============================================================================
# run_local_simulation.sh
# Anti-Jamming Drone System - Ultra-Lightweight Host Native Simulation Mode
# Runs entirely outside Docker to minimize CPU and RAM consumption.
# =============================================================================

# Colors for premium CLI output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "=========================================================="
echo "   AUTONOMOUS DRONE ANTI-JAMMING SYSTEM (SIMULATION)      "
echo "        Lightweight Local Host-Native Deployment          "
echo "=========================================================="
echo -e "${NC}"

# Check virtual environment
if [ ! -d "venv" ]; then
    echo -e "${RED}[ERROR] Local virtual environment 'venv' not found.${NC}"
    echo "Please create a virtual environment in the project root first:"
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Ensure output folders exist
mkdir -p logs models experiments

# Function to check if a port is in use
check_port() {
    local port=$1
    if lsof -i :"$port" >/dev/null 2>&1; then
        return 0 # In use
    else
        return 1 # Free
    fi
}

# Verify ports are free before launching
for port in 8000 8080 8090; do
    if check_port "$port"; then
        echo -e "${RED}[ERROR] Port $port is already in use.${NC}"
        echo "Please close any existing processes using port $port before running the simulation."
        exit 1
    fi
done

# Ensure mode is set to simulation in config/mode.yaml
echo -e "${BLUE}[INFO] Configuring system execution mode to 'simulation'...${NC}"
if [ -f "config/mode.yaml" ]; then
    # Simple replacement or ensure config/mode.yaml sets mode to simulation
    sed -i '' 's/mode: real/mode: simulation/g' config/mode.yaml 2>/dev/null || sed -i 's/mode: real/mode: simulation/g' config/mode.yaml
fi

# Lock file for PIDs
PID_FILE=".local_sim.pids"
rm -f "$PID_FILE"

# Helper function to launch a process
launch_service() {
    local name=$1
    local cmd=$2
    local log=$3

    echo -e "${YELLOW}[STARTING] ${name}...${NC}"
    eval "$cmd" > "$log" 2>&1 &
    local pid=$!
    
    # Check if process died immediately
    sleep 0.8
    if kill -0 "$pid" >/dev/null 2>&1; then
        echo -e "${GREEN}[SUCCESS] ${name} running (PID: ${pid}) -> Log: ${log}${NC}"
        echo "$name:$pid" >> "$PID_FILE"
    else
        echo -e "${RED}[FAILED] ${name} failed to start. Check log file: ${log}${NC}"
        # Print last few lines of log
        tail -n 5 "$log"
        ./stop_local_simulation.sh
        exit 1
    fi
}

# 1. Start Federated Learning Server
launch_service "FL-Server" "venv/bin/python fl/server.py --rounds 20" "logs/fl_server.log"

# 2. Start Federated Learning Clients
launch_service "FL-Client-1" "venv/bin/python fl/client.py --client_id drone_1 --server_address localhost:8090" "logs/fl_client_1.log"
launch_service "FL-Client-2" "venv/bin/python fl/client.py --client_id drone_2 --server_address localhost:8090" "logs/fl_client_2.log"
launch_service "FL-Client-3" "venv/bin/python fl/client.py --client_id drone_3 --server_address localhost:8090 --jammed" "logs/fl_client_3.log"

# 3. Start Mock SDN Controller
launch_service "Mock-SDN" "venv/bin/python sdn/mock_sdn.py" "logs/mock_sdn.log"

# 4. Start API Server (FastAPI)
launch_service "API-Server" "venv/bin/python -m uvicorn api.server:app --host 0.0.0.0 --port 8000" "logs/api_server.log"

# 5. Start Orchestrator Control Loop
launch_service "Orchestrator-Loop" "venv/bin/python -m orchestrator.loop" "logs/orchestrator.log"

echo -e "\n${GREEN}==========================================================${NC}"
echo -e "${GREEN}   ALL LOCAL SIMULATION SERVICES ARE UP AND RUNNING!      ${NC}"
echo -e "${GREEN}==========================================================${NC}"
echo -e "Use the dashboard at: ${CYAN}http://localhost:5173/${NC}"
echo -e "To shut down the simulation, run: ${MAGENTA}./stop_local_simulation.sh${NC}\n"
