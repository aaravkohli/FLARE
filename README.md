# Autonomous Anti-Jamming Drone Communication System

A research-grade prototype combining **Federated Learning**, **Reinforcement Learning**, and **Software Defined Networking** for autonomous anti-jamming in drone networks.

---

## Component Labels

| Component | Label | Description |
|-----------|-------|-------------|
| `simulation/generator.py` | **SIMULATED** | Synthetic RF data — no real hardware needed |
| `simulation/jammer.py` | **SIMULATED** | CLI jamming tool — writes jam_state.json |
| `sdn/mock_sdn.py` | **MOCK** | Simulates SDN flow rules — no OpenFlow required |
| `sdn/controller.py` | **REAL** | Full Ryu OpenFlow 1.3 controller |
| `sdn/flow_manager.py` | **REAL** | Requires Ryu + Open vSwitch |
| `fl/model.py` | **REAL** | PyTorch BiLSTM + Attention |
| `fl/client.py` | **REAL** | Flower FL client |
| `fl/server.py` | **REAL** | Flower FL server + SecureFedAvg |
| `fl/aggregator.py` | **REAL** | Gradient clipping + trimmed mean |
| `rl/env.py` | **REAL** | Custom Gymnasium environment |
| `rl/train.py` | **REAL** | Stable-Baselines3 DQN training |
| `rl/agent.py` | **REAL** | Inference wrapper |
| `api/server.py` | **REAL** | FastAPI `/predict` endpoint |
| `orchestrator/loop.py` | **REAL** | Main control loop |
| `baselines/` | **OPTIONAL** | Research comparison baselines |
| `redis` service | **OPTIONAL** | Pub/sub — not required |

---

## Architecture

```
Drone Layer (Edge)          FL Aggregation          RL Decision          SDN Control
┌─────────────────┐        ┌──────────────┐        ┌───────────┐        ┌──────────┐
│ Metrics:        │        │ FedAvg /     │        │ Double    │        │ Ryu /    │
│ RSSI,PDR,SINR   │ ──FL──▶│ FedProx      │──score▶│ DQN Agent │──path─▶│ Mock SDN │
│ latency,loss    │        │ + Sec.Aggr.  │        │           │        │          │
│ BiLSTM model    │        └──────────────┘        └───────────┘        └──────────┘
└─────────────────┘
```

---

## Quick Start — SIMULATION Mode (Recommended)

### Prerequisites
- Python 3.11+
- Docker + Docker Compose

### Step 1: Install dependencies (local dev)

```bash
cd /Users/aaravkohli/Capstone
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Train the RL agent (one-time offline training)

```bash
python rl/train.py --timesteps 50000
# Saves: models/rl_model.zip
```

### Step 3: Train the FL model (one-time offline training)

In three separate terminals:

```bash
# Terminal 1 — FL server
python fl/server.py --rounds 10

# Terminal 2 — Client 1
python fl/client.py --client_id drone_1 --server_address 127.0.0.1:8090

# Terminal 3 — Client 2
python fl/client.py --client_id drone_2 --server_address 127.0.0.1:8090
# Saves: models/fl_model.pth
```

### Step 4: Start the Mock SDN controller

```bash
python sdn/mock_sdn.py
# Listening on http://localhost:8080
```

### Step 5: Start the API server + Orchestrator

```bash
# Terminal A — API
uvicorn api.server:app --host 0.0.0.0 --port 8000

# Terminal B — Orchestrator
python -m orchestrator.loop
```

### Step 6: Run a jamming test

```bash
# In a new terminal, jam the Direct path for 10 seconds
python simulation/jammer.py --jam direct --duration 10
```

Watch the orchestrator logs — it should switch away from `direct` within < 1 second.

### Step 7: Query the API

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"path_scores": [0.9, 0.2, 0.1]}'
```

**Expected response:**
```json
{
  "action_id": 1,
  "path_name": "satellite",
  "threat_level": "HIGH",
  "fl_confidence": 0.87,
  "attack_type": "barrage",
  "timestamp": 1714399501.2
}
```

---

## Docker — Full Stack (Simulation Mode)

```bash
# Build and start all services
docker-compose -f docker-compose.dev.yml up --build

# Trigger jamming (from host)
python simulation/jammer.py --jam direct --duration 10

# View experiment log
docker exec api-orchestrator cat experiments/run_<id>.csv

# View SDN flow table
curl http://localhost:8080/sdn/flows
```

---

## Real Mode (Ryu + Open vSwitch)

### Prerequisites
- `openvswitch-switch` installed on host
- Mininet (optional, for virtual topology)

### OVS Setup

```bash
# Create a bridge and connect to Ryu controller
sudo ovs-vsctl add-br br0
sudo ovs-vsctl set-controller br0 tcp:127.0.0.1:6633
sudo ovs-vsctl add-port br0 eth1  # add your network interfaces
```

### Start Ryu controller

```bash
ryu-manager sdn/controller.py --ofp-tcp-listen-port 6633
```

### Docker (REAL mode)

```bash
docker-compose -f docker-compose.prod.yml up --build
```

---

## Running Baselines (Research Comparison)

```bash
# FL baselines (Logistic Regression vs Random Forest vs BiLSTM)
python baselines/fl_baseline.py

# RL baselines (Greedy vs Static vs DQN)
python baselines/rl_baseline.py
```

---

## Experiment Output

Logs are written to:
- `logs/orchestrator.log` — structured JSON per step
- `experiments/run_<id>.csv` — per-step CSV
- `experiments/experiment.db` — SQLite (multi-run comparison)

### Example CSV row

```
timestamp,run_id,step,action_id,path_name,threat_level,reward,recovery_ms,packet_loss,fl_confidence,attack_type
1714399501.2,a1b2c3d4,42,1,satellite,HIGH,0.72,480.5,0.11,0.88,barrage
```

---

## Config Reference

| File | Purpose |
|------|---------|
| `config/mode.yaml` | Switch between simulation / real |
| `config/fl_config.yaml` | FL model, training, security params |
| `config/rl_config.yaml` | RL env, reward weights, training |
| `config/sdn_config.yaml` | SDN host, ports, failover priority |

---

## Project Structure

```
Capstone/
├── config/          # All YAML configuration
├── schemas/         # JSON Schema data contracts
├── fl/              # Federated Learning (BiLSTM + Flower)
├── rl/              # Reinforcement Learning (DQN + Gymnasium)
├── sdn/             # SDN (Ryu real + mock stub)
├── api/             # FastAPI server
├── orchestrator/    # Main control loop
├── simulation/      # Synthetic metric generator + jammer
├── baselines/       # Research comparison baselines
├── models/          # Saved model weights
├── logs/            # Runtime logs
├── experiments/     # CSV + SQLite experiment data
├── datasets/        # Dataset documentation
├── docker/          # Dockerfiles
├── docker-compose.dev.yml   # Simulation mode
├── docker-compose.prod.yml  # Real mode (Ryu + OVS)
└── requirements.txt
```
