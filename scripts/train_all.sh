#!/usr/bin/env bash
# =============================================================================
# scripts/train_all.sh
# End-to-end training pipeline: download → preprocess → FL → RL → evaluate
#
# Usage:
#   bash scripts/train_all.sh                      # full run (RadioML + DroneRF)
#   bash scripts/train_all.sh --synthetic-only     # skip downloads, use synthetic data
#   bash scripts/train_all.sh --fl-rounds 5 --rl-steps 5000   # quick smoke test
#
# Requirements: venv activated, requirements installed
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── Defaults ──────────────────────────────────────────────────────────────────
FL_ROUNDS=30
RL_STEPS=100000
SYNTHETIC_ONLY=false
SKIP_PREPROCESS=false
FL_SERVER_ADDR="127.0.0.1:8090"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fl-rounds)      FL_ROUNDS="$2";  shift 2;;
    --rl-steps)       RL_STEPS="$2";   shift 2;;
    --synthetic-only) SYNTHETIC_ONLY=true; shift;;
    --skip-preprocess) SKIP_PREPROCESS=true; shift;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $*"; }
ok()   { echo -e "${GREEN}[$(date '+%H:%M:%S')] ✓${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${NC} $*"; }
fail() { echo -e "${RED}[$(date '+%H:%M:%S')] ✗${NC} $*"; exit 1; }

# ── Auto-detect pre-existing processed data ───────────────────────────────────
# _processed_exist() {
#   [ -f "datasets/processed/drone_1_train.csv" ] && \
#   [ -f "datasets/processed/drone_2_train.csv" ] && \
#   [ -f "datasets/processed/drone_3_train.csv" ] && \
#   [ -f "datasets/processed/test.csv" ]
# }
# if _processed_exist; then
#   warn "Processed CSVs already exist — skipping download & preprocess steps."
#   warn "  (Pass --skip-preprocess=false to force reprocessing.)"
#   SKIP_PREPROCESS=true
# fi

# ── Sanity checks ─────────────────────────────────────────────────────────────
log "Checking Python environment..."
python -c "import torch, flwr, gymnasium, stable_baselines3, pandas, sklearn" \
  || fail "Missing dependencies. Run: pip install -r requirements.txt"
ok "Dependencies OK"

# ── Step 1: Dataset download ──────────────────────────────────────────────────
if [ "$SKIP_PREPROCESS" = true ]; then
  ok "═══ STEP 1/5: Dataset Download — SKIPPED (data already preprocessed) ═══"
else
  log "═══ STEP 1/5: Dataset Download ═══"
  if [ "$SYNTHETIC_ONLY" = true ]; then
    warn "Synthetic-only mode: skipping real downloads."
    python datasets/download.py --synthetic-only
  else
    python datasets/download.py
  fi
fi

# ── Step 2: Preprocessing ────────────────────────────────────────────────────
if [ "$SKIP_PREPROCESS" = true ]; then
  ok "═══ STEP 2/5: Preprocessing — SKIPPED (data already preprocessed) ═══"
else
  log "═══ STEP 2/5: Preprocessing ═══"
  if [ "$SYNTHETIC_ONLY" = true ]; then
    python datasets/preprocess.py --no-radioml --no-dronerf
  else
    python datasets/preprocess.py
  fi
fi

# Resolve the authorized fleet only after preprocessing so client identity and
# data ownership use the same current registry snapshot.
CLIENT_IDS=()
while IFS= read -r client_id; do
  CLIENT_IDS+=("$client_id")
done < <(python -c "from fleet.registry import active_drone_ids; print(*active_drone_ids(), sep='\n')")
[ "${#CLIENT_IDS[@]}" -ge 2 ] || fail "Federated training requires at least two active drones."
for client_id in "${CLIENT_IDS[@]}"; do
  [ -f "datasets/processed/${client_id}_train.csv" ] \
    || fail "Missing processed file: ${client_id}_train.csv"
done
[ -f "datasets/processed/test.csv" ] || fail "Missing processed file: test.csv"
ok "Preprocessed datasets ready."

# ── Step 3: Federated Learning ───────────────────────────────────────────────
log "═══ STEP 3/5: Federated Learning ($FL_ROUNDS rounds) ═══"

# Kill any stale FL processes
pkill -f "fl/server.py" 2>/dev/null || true
pkill -f "fl/client.py" 2>/dev/null || true
sleep 1

# Start FL server in background
python fl/server.py --rounds "$FL_ROUNDS" --output models/fl_candidate.pth &
FL_SERVER_PID=$!
log "FL server started (PID $FL_SERVER_PID), waiting 3s for startup..."
sleep 3

# Start one strict real-data client per active registry identity.
CLIENT_PIDS=()
for client_id in "${CLIENT_IDS[@]}"; do
  log "  Starting FL client ${client_id}..."
  python fl/client.py \
    --client_id "$client_id" \
    --real \
    --require-real-data \
    --server_address "$FL_SERVER_ADDR" \
    > "logs/fl_client_${client_id}.log" 2>&1 &
  CLIENT_PIDS+=($!)
done

# Wait for all clients and server
log "Waiting for FL training to complete..."
for pid in "${CLIENT_PIDS[@]}"; do
  wait "$pid" || warn "FL client exited with non-zero status."
done
wait "$FL_SERVER_PID" || warn "FL server exited with non-zero status."

[ -f "models/fl_candidate.pth" ] || fail "FL candidate not saved. Check logs/fl_client_*.log"
ok "FL training complete → models/fl_candidate.pth (not deployed)"

# ── Step 4: RL Training ───────────────────────────────────────────────────────
log "═══ STEP 4/5: RL Training ($RL_STEPS steps) ═══"
python -m rl.traces \
  --scenario iid \
  --episodes 200 \
  --steps 500 \
  --seed 42 \
  --output datasets/processed/rl_trace_iid_train.csv
python -m rl.traces \
  --scenario iid \
  --episodes 50 \
  --steps 500 \
  --seed 42000 \
  --output datasets/processed/rl_trace_iid_eval.csv
python rl/train.py --timesteps "$RL_STEPS" --trace-data

[ -f "models/best_model.zip" ] || fail "RL validation-best candidate not saved."
ok "RL training complete → models/best_model.zip (not deployed)"

# ── Step 5: Evaluation ────────────────────────────────────────────────────────
log "═══ STEP 5/5: Evaluation ═══"
python scripts/evaluate.py \
  --model-path models/fl_candidate.pth \
  --rl-model-path models/best_model.zip

ok "═══ Pipeline complete! Results in results/ ═══"
echo ""
echo "  FL candidate:    models/fl_candidate.pth"
echo "  RL candidate:    models/best_model.zip"
echo "  Round metrics:   results/fl_round_metrics.csv"
echo "  Eval evidence:   results/runs/model_evaluation/<run-id>/"
echo "  Plots:           results/plots/"
echo "  Deployment:      use scripts/promote_checkpoint.py after evidence review"
