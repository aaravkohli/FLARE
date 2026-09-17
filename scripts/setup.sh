#!/usr/bin/env bash
# =============================================================================
# scripts/setup.sh — FLARE One-Command Local Environment Setup (macOS / Linux)
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
cd "$REPO_ROOT"

printf '\n%b\n' "${CYAN}${BOLD}==========================================================${NC}"
printf '%b\n' "${CYAN}${BOLD}       FLARE AUTOMATED ENVIRONMENT SETUP${NC}"
printf '%b\n' "${CYAN}${BOLD}==========================================================${NC}\n"

# 1. Check Python
printf '%b\n' "${BOLD}[1/5] Checking Python...${NC}"
PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null || true)
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || true)
        if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_BIN="$candidate"
            printf '%b\n' "  ${GREEN}✔ Found Python ${version} (${candidate})${NC}"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    printf '%b\n' "  ${RED}✖ Python 3.10 or higher is required.${NC}"
    exit 1
fi

# 2. Check Node & npm
printf '%b\n' "\n${BOLD}[2/5] Checking Node.js & npm...${NC}"
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    node_version=$(node -v)
    npm_version=$(npm -v)
    printf '%b\n' "  ${GREEN}✔ Found Node.js ${node_version} and npm ${npm_version}${NC}"
else
    printf '%b\n' "  ${YELLOW}⚠ Node.js or npm not found in PATH.${NC}"
    printf '%b\n' "    Frontend dashboard requires Node.js >= 18. Backend simulation can still run headless.${NC}"
fi

# 3. Setup Python Virtual Environment
printf '%b\n' "\n${BOLD}[3/5] Setting up Python virtual environment...${NC}"
if [ ! -d "venv" ]; then
    printf '%b\n' "  Creating venv in ${REPO_ROOT}/venv..."
    "$PYTHON_BIN" -m venv venv
    printf '%b\n' "  ${GREEN}✔ Virtual environment created.${NC}"
else
    printf '%b\n' "  ${GREEN}✔ Existing virtual environment found at venv/.${NC}"
fi

VENV_PYTHON="${REPO_ROOT}/venv/bin/python"
VENV_PIP="${REPO_ROOT}/venv/bin/pip"

printf '%b\n' "  Installing / verifying Python dependencies from requirements.txt..."
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet -r requirements.txt
printf '%b\n' "  ${GREEN}✔ Python dependencies installed successfully.${NC}"

# 4. Setup Frontend Dependencies
printf '%b\n' "\n${BOLD}[4/5] Setting up Frontend dependencies...${NC}"
if command -v npm >/dev/null 2>&1 && [ -d "frontend-react" ]; then
    (
        cd frontend-react
        if [ ! -d "node_modules" ]; then
            printf '%b\n' "  Installing npm dependencies in frontend-react..."
            npm install --silent
        else
            printf '%b\n' "  ${GREEN}✔ node_modules already present in frontend-react.${NC}"
        fi
        printf '%b\n' "  Building production frontend bundle..."
        npm run build --silent
        printf '%b\n' "  ${GREEN}✔ Frontend production bundle built successfully in frontend-react/dist/.${NC}"
    )
else
    printf '%b\n' "  ${YELLOW}Skipping frontend dependency installation (npm unavailable).${NC}"
fi

# 5. Environment & Directories
printf '%b\n' "\n${BOLD}[5/5] Configuring environment & runtime directories...${NC}"
mkdir -p logs models experiments
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    printf '%b\n' "  ${GREEN}✔ Created default .env from .env.example.${NC}"
else
    printf '%b\n' "  ${GREEN}✔ .env configuration file already exists.${NC}"
fi

printf '\n%b\n' "${GREEN}${BOLD}==========================================================${NC}"
printf '%b\n' "${GREEN}${BOLD}       SETUP COMPLETE — READY TO LAUNCH${NC}"
printf '%b\n' "${GREEN}${BOLD}==========================================================${NC}"
printf '\nTo start FLARE services, run:\n'
printf '  %b./scripts/start.sh%b        (starts backend services and frontend)\n' "${CYAN}" "${NC}"
printf '  or:\n'
printf '  %bpython scripts/start.py%b   (cross-platform launcher)\n' "${CYAN}" "${NC}"
printf '  or:\n'
printf '  %bdocker compose up --build%b (containerized)\n\n' "${CYAN}" "${NC}"
