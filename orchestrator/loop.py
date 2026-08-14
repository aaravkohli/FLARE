"""
orchestrator/loop.py — [REAL]
Main control loop for the anti-jamming drone system.

Each iteration (default 0.5s):
  1. Collect RF metrics  (simulation/generator.py or live sensor)
  2. Run FL inference    (fl/model.py) → per-path threat scores
  3. Run RL decision     (rl/agent.py) → best path
  4. Push to SDN         (mock_sdn.py or Ryu) → install flow rule
  5. Log experiment data (CSV + SQLite)

Features:
  - Structured JSON logging
  - Retry with exponential backoff (tenacity)
  - Per-call timeouts (asyncio)
  - Graceful degradation (last-known-good cache, 5s staleness limit)
  - SIGINT / SIGTERM handling

Usage:
  python orchestrator/loop.py
"""

import asyncio
import csv
from contextlib import closing
from functools import partial
import json
import logging
import os
import signal
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path when running as `python -m orchestrator.loop`
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import numpy as np
import torch
import yaml

_BASE = Path(__file__).parent.parent
_MODE_CFG  = yaml.safe_load((_BASE / "config" / "mode.yaml").read_text())
_FL_CFG    = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_SDN_CFG   = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())

MODE = os.getenv("MODE", _MODE_CFG["mode"]).strip().lower()
if MODE not in _MODE_CFG or MODE not in {"simulation", "real"}:
    raise RuntimeError("MODE must be either 'simulation' or 'real'")
LOOP_INTERVAL = _MODE_CFG[MODE]["loop_interval_s"]
SDN_HOST  = os.getenv("SDN_HOST", _SDN_CFG["controller"]["host"])
SDN_PORT  = _SDN_CFG["controller"]["port"]
SDN_URL   = f"http://{SDN_HOST}:{SDN_PORT}/sdn/route"
SDN_TIMEOUT = _SDN_CFG["controller"]["timeout_s"]
SDN_API_TOKEN = os.getenv("AJ_SDN_TOKEN", "antijam-development-sdn-token")
SENSOR_API_URL = os.getenv("AJ_SENSOR_API_URL", "http://localhost:9000").rstrip("/")

# Experiment session
RUN_ID = str(uuid.uuid4())[:8]

# Paths
os.makedirs(_BASE / "logs", exist_ok=True)
os.makedirs(_BASE / "experiments", exist_ok=True)

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": record.levelname,
            "component": "ORCHESTRATOR",
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log.update(record.extra)
        return json.dumps(log)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_JsonFormatter())
_fhandler = logging.FileHandler(_BASE / "logs" / "orchestrator.log")
_fhandler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler, _fhandler])
logger = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Experiment logging: CSV + SQLite
# ---------------------------------------------------------------------------

_CSV_PATH = _BASE / "experiments" / f"run_{RUN_ID}.csv"
_DB_PATH  = _BASE / "experiments" / "experiment.db"
_CSV_FIELDS = [
    "timestamp", "run_id", "step", "drone_id", "action_id", "path_name",
    "threat_level", "reward", "recovery_ms", "packet_loss",
    "fl_confidence", "attack_type",
]
_DB_COLUMNS = [
    "run_id", "timestamp", "step", "drone_id", "action_id", "path_name",
    "threat_level", "reward", "recovery_ms", "packet_loss",
    "fl_confidence", "attack_type",
]
_PATH_NAMES = ["direct", "satellite", "mesh"]


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn

def _init_experiment_store():
    # CSV header
    with open(_CSV_PATH, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=_CSV_FIELDS).writeheader()

    # SQLite table
    with closing(_connect_db()) as conn:
        with conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT, timestamp REAL, step INTEGER, drone_id TEXT,
                    action_id INTEGER, path_name TEXT, threat_level TEXT,
                    reward REAL, recovery_ms REAL, packet_loss REAL,
                    fl_confidence REAL, attack_type TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_drone_id_id "
                "ON runs (drone_id, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_run_id_step "
                "ON runs (run_id, step)"
            )


def _log_experiment(row: dict):
    """Compatibility wrapper for callers that persist one row."""
    _log_experiments([row])


def _log_experiments(rows: list[dict]) -> None:
    """Persist one control-loop batch with one CSV open and one DB transaction."""
    if not rows:
        return

    with open(_CSV_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=_CSV_FIELDS).writerows(rows)

    placeholders = ",".join("?" for _ in _DB_COLUMNS)
    columns = ",".join(_DB_COLUMNS)
    values = [[row[column] for column in _DB_COLUMNS] for row in rows]
    with closing(_connect_db()) as conn:
        with conn:
            conn.executemany(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders})",
                values,
            )


# ---------------------------------------------------------------------------
# FL Inference
# ---------------------------------------------------------------------------

def _load_fl_model():
    """Load the global FL model. Returns None if not yet trained."""
    model_path = _BASE / _FL_CFG["paths"]["model_save"]
    if not model_path.exists():
        logger.warning("FL model not found at %s. Using random threat scores.", model_path)
        return None
    from fl.model import build_model
    model = build_model(_FL_CFG["model"])
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()
    return model


def _fl_infer(model, metrics: dict) -> dict:
    """
    Run FL model inference on the given metrics snapshot.
    Returns threat_scores dict.
    """
    return _fl_infer_batch(model, {"single": metrics})["single"]


def _fl_infer_batch(model, metrics_by_drone: dict[str, dict]) -> dict[str, dict]:
    """Run every drone/path sequence in one model forward pass."""
    from collections import Counter
    from fl.model import ATTACK_CLASSES
    from simulation.generator import metrics_to_tensor

    entries: list[tuple[str, int]] = []
    sequences = []
    for drone_id, metrics in metrics_by_drone.items():
        for path_index, sequence in enumerate(
            metrics_to_tensor(metrics, seq_len=_FL_CFG["model"]["sequence_len"])
        ):
            entries.append((drone_id, path_index))
            sequences.append(sequence)

    if not sequences:
        return {}

    x = torch.tensor(np.stack(sequences), dtype=torch.float32)
    with torch.no_grad():
        output = model(x)

    raw_scores = output.path_scores.mean(dim=1).cpu().numpy()
    confidences = output.confidence.squeeze(-1).cpu().numpy()
    attack_indices = output.attack_logits.argmax(dim=1).cpu().numpy()

    grouped = {
        drone_id: {"path_scores": [], "confidences": [], "attack_types": []}
        for drone_id in metrics_by_drone
    }
    for batch_index, (drone_id, path_index) in enumerate(entries):
        score = float(raw_scores[batch_index])
        if metrics_by_drone[drone_id]["paths"][path_index]["pdr"] < 0.4:
            score = max(score, 0.95)
        grouped[drone_id]["path_scores"].append(score)
        grouped[drone_id]["confidences"].append(float(confidences[batch_index]))
        grouped[drone_id]["attack_types"].append(
            ATTACK_CLASSES[int(attack_indices[batch_index])]
        )

    return {
        drone_id: {
            "path_scores": values["path_scores"],
            "confidence": float(np.mean(values["confidences"])),
            "attack_type": Counter(values["attack_types"]).most_common(1)[0][0],
        }
        for drone_id, values in grouped.items()
    }


def _random_threat_scores() -> dict:
    """Fallback when FL model unavailable."""
    return {
        "path_scores": list(np.random.uniform(0.0, 0.5, 3).tolist()),
        "confidence": 0.5,
        "attack_type": "unknown",
    }


# ---------------------------------------------------------------------------
# SDN Push (with retry)
# ---------------------------------------------------------------------------

async def _push_to_sdn(client: httpx.AsyncClient, path_name: str, drone_id: str) -> dict:
    """
    POST routing decision to SDN controller.
    Retries up to 3 times with exponential backoff using native async.
    """
    payload = {"path_name": path_name, "drone_id": drone_id, "action_id": 0}
    last_err = None
    for attempt, delay in enumerate([0.0, 0.1, 0.4]):
        try:
            if delay:
                await asyncio.sleep(delay)
            resp = await client.post(
                SDN_URL,
                json=payload,
                headers={"Authorization": f"Bearer {SDN_API_TOKEN}"},
                timeout=SDN_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            last_err = e
            logger.warning("SDN push attempt %d failed: %s", attempt + 1, e)
    raise last_err


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self):
        self._running = True
        self._fl_model = _load_fl_model()
        self._step = 0
        
        self.DRONES = ["drone_1", "drone_2", "drone_3"]
        self._prev_path = {d: None for d in self.DRONES}
        self._prev_path_time = {d: None for d in self.DRONES}
        self._last_good_decision = {d: None for d in self.DRONES}
        self._last_good_ts = {d: 0.0 for d in self.DRONES}

        # Keep independent stateful RL wrappers per drone. A single shared wrapper
        # would leak previous-action/reward state between drone decisions.
        self._rl_agents = {}
        rl_path = _BASE / "models" / "rl_model.zip"
        if rl_path.exists():
            from rl.agent import RLAgent
            try:
                for drone_id in self.DRONES:
                    self._rl_agents[drone_id] = RLAgent(str(rl_path))
            except Exception as exc:
                # A stale or incompatible checkpoint must not take down the
                # sensing/control loop. Keep all drones on the deterministic
                # greedy policy rather than leaving a partially loaded fleet.
                self._rl_agents.clear()
                logger.warning(
                    "RL checkpoint could not be loaded (%s). Using greedy fallback.",
                    exc,
                )
            else:
                logger.info("RL agents loaded for %d drones.", len(self._rl_agents))
        else:
            logger.warning("RL model not found. Using greedy fallback.")
        self._prev_rewards = {drone_id: 0.0 for drone_id in self.DRONES}

        _init_experiment_store()
        logger.info("Orchestrator started | run_id=%s | mode=%s | interval=%.1fs",
                    RUN_ID, MODE, LOOP_INTERVAL)

    def _greedy_decision(self, path_scores: list) -> dict:
        action = int(min(range(3), key=lambda i: path_scores[i]))
        paths = ["direct", "satellite", "mesh"]
        return {"action_id": action, "path_name": paths[action]}

    def _compute_reward(self, path_scores: list, action: int) -> float:
        """Simplified reward for logging (full reward computed in RL env)."""
        threat = path_scores[action]
        return round(1.0 - threat - 0.1 * action, 4)  # penalise higher-index paths slightly

    def _recovery_ms(self, drone_id: str, new_path: str) -> Optional[float]:
        """Return recovery time in ms if path switched, else None."""
        prev = self._prev_path[drone_id]
        if prev is not None and new_path != prev:
            prev_t = self._prev_path_time[drone_id]
            if prev_t is not None:
                return round((time.time() - prev_t) * 1000, 1)
        return None

    async def _collect_metrics(self, client: httpx.AsyncClient) -> dict[str, dict]:
        if MODE == "simulation":
            from simulation.generator import generate_swarm_metrics
            return generate_swarm_metrics()

        async def fetch_one(drone_id: str) -> tuple[str, dict]:
            try:
                response = await client.get(
                    f"{SENSOR_API_URL}/metrics",
                    params={"drone_id": drone_id},
                    timeout=1.0,
                )
                response.raise_for_status()
                return drone_id, response.json()
            except Exception as exc:
                logger.warning(
                    "Live metrics unavailable for %s (%s). Using synthetic fallback.",
                    drone_id,
                    exc,
                )
                from simulation.generator import generate_metrics
                return drone_id, generate_metrics(drone_id)

        pairs = await asyncio.gather(*(fetch_one(drone_id) for drone_id in self.DRONES))
        return dict(pairs)

    async def _run_step(self, client: httpx.AsyncClient) -> None:
        self._step += 1
        t_start = time.time()
        metrics_by_drone = await self._collect_metrics(client)

        try:
            async with asyncio.timeout(0.5):
                if self._fl_model:
                    threats_by_drone = await asyncio.to_thread(
                        _fl_infer_batch,
                        self._fl_model,
                        metrics_by_drone,
                    )
                else:
                    threats_by_drone = {
                        drone_id: _random_threat_scores()
                        for drone_id in self.DRONES
                    }
        except asyncio.TimeoutError:
            logger.warning("Batched FL inference timed out. Using random scores.")
            threats_by_drone = {
                drone_id: _random_threat_scores()
                for drone_id in self.DRONES
            }

        rows = []
        for drone_id in self.DRONES:
            metrics = metrics_by_drone[drone_id]
            threat = threats_by_drone[drone_id]

            path_scores = threat["path_scores"]
            max_latencies = [100.0, 300.0, 600.0]
            path_latencies = [
                min(1.0, max(0.0, metrics["paths"][idx]["latency"] / max_latencies[idx]))
                for idx in range(3)
            ]
            path_losses = [
                min(1.0, max(0.0, metrics["paths"][idx]["packet_loss"]))
                for idx in range(3)
            ]

            # Step 3: RL decision
            try:
                async with asyncio.timeout(0.1):
                    loop = asyncio.get_running_loop()
                    rl_agent = self._rl_agents.get(drone_id)
                    if rl_agent:
                        predict = partial(
                            rl_agent.predict,
                            path_scores,
                            reward=self._prev_rewards[drone_id],
                            path_latencies=path_latencies,
                            path_losses=path_losses,
                        )
                        decision = await loop.run_in_executor(
                            None, predict
                        )
                    else:
                        decision = self._greedy_decision(path_scores)
            except asyncio.TimeoutError:
                logger.warning("RL inference timed out for %s. Using greedy.", drone_id)
                decision = self._greedy_decision(path_scores)

            action_id  = decision["action_id"]
            requested_path = decision["path_name"]
            threat_lvl = decision.get("threat_level", "UNKNOWN")

            # Graceful degradation: use last known good if stale
            current_ts = time.time()
            if self._last_good_decision[drone_id] and (current_ts - self._last_good_ts[drone_id] > 5.0):
                logger.warning("Decision cache stale (>5s) for %s. Using fallback.", drone_id)
                requested_path = "mesh"
                action_id = _PATH_NAMES.index(requested_path)

            # Step 4: Push to SDN
            sdn_ok = False
            path_name = self._prev_path[drone_id] or requested_path
            try:
                sdn_resp = await _push_to_sdn(client, requested_path, drone_id)
                sdn_ok = True
                installed_path = sdn_resp.get("installed_path", requested_path)
                path_name = installed_path if installed_path in _PATH_NAMES else requested_path
                action_id = _PATH_NAMES.index(path_name)
                self._last_good_decision[drone_id] = {
                    **decision,
                    "action_id": action_id,
                    "path_name": path_name,
                }
                self._last_good_ts[drone_id] = current_ts
            except Exception as e:
                logger.error("SDN push failed for %s: %s. Maintaining current path.", drone_id, e)
                if path_name in _PATH_NAMES:
                    action_id = _PATH_NAMES.index(path_name)

            # Step 5: Metrics
            reward = self._compute_reward(path_scores, action_id)
            self._prev_rewards[drone_id] = reward
            recovery = self._recovery_ms(drone_id, path_name)
            packet_loss = round(metrics["paths"][action_id]["packet_loss"], 4)

            row = {
                "timestamp":    round(current_ts, 3),
                "run_id":       RUN_ID,
                "step":         self._step,
                "drone_id":     drone_id,
                "action_id":    action_id,
                "path_name":    path_name,
                "threat_level": threat_lvl,
                "reward":       reward,
                "recovery_ms":  recovery,
                "packet_loss":  packet_loss,
                "fl_confidence": round(threat["confidence"], 4),
                "attack_type":  threat["attack_type"],
            }
            rows.append(row)

            # Update path tracking
            if sdn_ok and path_name != self._prev_path[drone_id]:
                self._prev_path[drone_id] = path_name
                self._prev_path_time[drone_id] = current_ts

        await asyncio.to_thread(_log_experiments, rows)

        elapsed_ms = round((time.time() - t_start) * 1000, 1)
        logger.info("step=%d | processed %d drones | elapsed=%s ms", self._step, len(self.DRONES), elapsed_ms)

    async def run(self):
        """Main async loop."""
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    await self._run_step(client)
                except Exception as e:
                    logger.exception("Unexpected error in step %d: %s", self._step, e)
                await asyncio.sleep(LOOP_INTERVAL)

    def stop(self):
        logger.info("Shutdown signal received. Stopping after current step.")
        self._running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    orch = Orchestrator()

    def _signal_handler(sig, frame):
        orch.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    asyncio.run(orch.run())
    logger.info("Orchestrator stopped. Experiment log: %s", _CSV_PATH)


if __name__ == "__main__":
    main()
