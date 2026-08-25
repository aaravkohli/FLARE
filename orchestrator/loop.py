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
from collections import deque
from contextlib import closing
from functools import partial
import hashlib
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

from schemas.decision_event import DecisionEvent, TelemetrySnapshot
from sdn.route_contract import (
    action_id_for_path,
    normalize_installed_path,
    validate_route_action,
)
from rl.reward import (
    MAX_LATENCY_MS,
    PATH_NAMES as SHARED_PATH_NAMES,
    REWARD_DEFINITION,
    RewardBreakdown,
    compute_routing_reward,
)
from rl.safety import constrain_route_action, resolve_safety_config

_BASE = Path(__file__).parent.parent
_MODE_CFG  = yaml.safe_load((_BASE / "config" / "mode.yaml").read_text())
_FL_CFG    = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_SDN_CFG   = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_RL_CFG    = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_SAFETY_CFG = _RL_CFG.get("safety", {})
_SAFETY_THRESHOLD, _ALL_UNSAFE_BEHAVIOR = resolve_safety_config(_SAFETY_CFG)

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
    "fl_confidence", "attack_type", "event_json",
]
_PATH_NAMES = list(SHARED_PATH_NAMES)


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _file_sha256(path: Path) -> Optional[str]:
    """Return a stable checkpoint identifier without loading the artifact."""
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


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
                    fl_confidence REAL, attack_type TEXT, event_json TEXT
                )
            """)
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "event_json" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN event_json TEXT")
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
        csv.DictWriter(f, fieldnames=_CSV_FIELDS).writerows(
            [
                {field: row.get(field) for field in _CSV_FIELDS}
                for row in rows
            ]
        )

    placeholders = ",".join("?" for _ in _DB_COLUMNS)
    columns = ",".join(_DB_COLUMNS)
    values = [[row.get(column) for column in _DB_COLUMNS] for row in rows]
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
        logger.warning("FL model not found at %s. Using heuristic threat scores.", model_path)
        return None
    try:
        from fl.checkpoint import validate_fl_checkpoint_metadata
        from fl.model import build_model

        validate_fl_checkpoint_metadata(
            model_path,
            model_config=_FL_CFG["model"],
            data_config=_FL_CFG.get("data", {}),
        )
        model = build_model(_FL_CFG["model"])
        model.load_state_dict(torch.load(model_path, map_location="cpu"))
        model.eval()
        return model
    except Exception as exc:
        logger.warning(
            "FL checkpoint could not be loaded (%s). Using heuristic threat scores.",
            exc,
        )
        return None


def _fl_infer(model, metrics: dict) -> dict:
    """
    Run FL model inference on the given metrics snapshot.
    Returns threat_scores dict.
    """
    return _fl_infer_batch(model, {"single": metrics})["single"]


def _fl_infer_batch(
    model,
    metrics_by_drone: dict[str, dict],
    histories_by_drone: Optional[dict[str, list[dict]]] = None,
) -> dict[str, dict]:
    """Run every drone/path sequence in one model forward pass."""
    from collections import Counter
    from fl.model import ATTACK_CLASSES
    from simulation.generator import metrics_to_tensor

    entries: list[tuple[str, int]] = []
    sequences = []
    for drone_id, metrics in metrics_by_drone.items():
        for path_index, sequence in enumerate(
            metrics_to_tensor(
                metrics,
                seq_len=_FL_CFG["model"]["sequence_len"],
                history=(histories_by_drone or {}).get(drone_id),
            )
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
        path_name = _PATH_NAMES[path_index]
        current_path = next(
            path
            for path in metrics_by_drone[drone_id]["paths"]
            if path["path_id"] == path_name
        )
        if current_path["pdr"] < 0.4:
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
            "source": "fl_model",
        }
        for drone_id, values in grouped.items()
    }


def _heuristic_threat_scores(metrics: dict) -> dict:
    """Deterministic, inspectable fallback when learned FL inference is unavailable."""
    scores = []
    for path in metrics["paths"]:
        rssi_severity = float(np.clip((-40.0 - float(path["rssi"])) / 80.0, 0.0, 1.0))
        sinr_severity = float(np.clip((20.0 - float(path["sinr"])) / 30.0, 0.0, 1.0))
        score = (
            0.15 * rssi_severity
            + 0.25 * (1.0 - float(path["pdr"]))
            + 0.15 * sinr_severity
            + 0.15 * min(float(path["latency"]) / 1000.0, 1.0)
            + 0.30 * float(path["packet_loss"])
        )
        if float(path["pdr"]) < 0.4:
            score = max(score, 0.95)
        scores.append(float(np.clip(score, 0.0, 1.0)))
    return {
        "path_scores": scores,
        "confidence": 0.4,
        "attack_type": "unknown",
        "source": "heuristic_fallback",
    }


# ---------------------------------------------------------------------------
# SDN Push (with retry)
# ---------------------------------------------------------------------------

async def _push_to_sdn(client: httpx.AsyncClient, path_name: str, drone_id: str) -> dict:
    """
    POST routing decision to SDN controller.
    Retries up to 3 times with exponential backoff using native async.
    """
    payload = {
        "path_name": path_name,
        "drone_id": drone_id,
        "action_id": action_id_for_path(path_name),
    }
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
            result = resp.json()
            reported_path = result.get("installed_path")
            if reported_path is None:
                raise RuntimeError("SDN response omitted installed_path")
            installed_path = normalize_installed_path(reported_path)
            installed_action_id = result.get(
                "installed_action_id",
                action_id_for_path(installed_path),
            )
            validate_route_action(installed_path, installed_action_id)
            result["installed_path"] = installed_path
            result["installed_action_id"] = installed_action_id
            return result
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
        self._fl_model_id = _file_sha256(
            _BASE / _FL_CFG["paths"]["model_save"]
        ) if self._fl_model is not None else None
        self._step = 0
        
        self.DRONES = ["drone_1", "drone_2", "drone_3"]
        self._prev_path = {d: None for d in self.DRONES}
        self._prev_path_time = {d: None for d in self.DRONES}
        self._last_good_decision = {d: None for d in self.DRONES}
        self._last_good_ts = {d: 0.0 for d in self.DRONES}
        sequence_len = int(_FL_CFG["model"]["sequence_len"])
        self._metric_history = {
            drone_id: deque(maxlen=sequence_len)
            for drone_id in self.DRONES
        }

        # Keep independent stateful RL wrappers per drone. A single shared wrapper
        # would leak previous-action/reward state between drone decisions.
        self._rl_agents = {}
        rl_path = _BASE / "models" / "rl_model.zip"
        self._rl_model_id = _file_sha256(rl_path)
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
        policy_action = int(min(range(3), key=lambda i: path_scores[i]))
        constrained = constrain_route_action(
            policy_action,
            path_scores,
            threat_threshold=_SAFETY_THRESHOLD,
        )
        action = constrained.action_id
        paths = ["direct", "satellite", "mesh"]
        max_score = max(path_scores)
        threat_level = "HIGH" if max_score >= 0.7 else ("MEDIUM" if max_score >= 0.4 else "LOW")
        return {
            "action_id": action,
            "path_name": paths[action],
            "threat_level": threat_level,
            "decision_source": "greedy_fallback",
            "observation": None,
            "safety_override": constrained.safety_override,
            "original_action_id": policy_action,
            "safe_action_mask": list(constrained.safe_action_mask),
            "no_safe_route": constrained.no_safe_route,
            "constraint_reason": constrained.constraint_reason,
            "safety_threshold": constrained.threat_threshold,
            "all_unsafe_behavior": constrained.all_unsafe_behavior,
        }

    def _compute_reward(
        self,
        path_scores: list,
        path_latencies: list,
        path_losses: list,
        action: int,
        previous_action: Optional[int],
    ) -> RewardBreakdown:
        """Use the exact reward implementation used by the training environment."""
        return compute_routing_reward(
            action,
            path_scores,
            path_latencies,
            path_losses,
            previous_action=previous_action,
        )

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
                metrics = response.json()
                metrics["source"] = "live"
                validated = TelemetrySnapshot.model_validate(metrics)
                return drone_id, validated.model_dump()
            except Exception as exc:
                logger.warning(
                    "Live metrics unavailable for %s (%s). Using synthetic fallback.",
                    drone_id,
                    exc,
                )
                from simulation.generator import generate_metrics
                metrics = generate_metrics(drone_id)
                metrics["source"] = "synthetic_fallback"
                return drone_id, metrics

        pairs = await asyncio.gather(*(fetch_one(drone_id) for drone_id in self.DRONES))
        return dict(pairs)

    async def _run_step(self, client: httpx.AsyncClient) -> None:
        self._step += 1
        t_start = time.time()
        metrics_by_drone = await self._collect_metrics(client)
        if not hasattr(self, "_metric_history"):
            sequence_len = int(_FL_CFG["model"]["sequence_len"])
            self._metric_history = {
                drone_id: deque(maxlen=sequence_len)
                for drone_id in self.DRONES
            }
        for drone_id, metrics in metrics_by_drone.items():
            self._metric_history[drone_id].append(metrics)
        histories_by_drone = {
            drone_id: list(history)
            for drone_id, history in self._metric_history.items()
        }

        fl_fallback_reason = "fl_model_unavailable" if not self._fl_model else None
        try:
            async with asyncio.timeout(0.5):
                if self._fl_model:
                    threats_by_drone = await asyncio.to_thread(
                        _fl_infer_batch,
                        self._fl_model,
                        metrics_by_drone,
                        histories_by_drone,
                    )
                else:
                    threats_by_drone = {
                        drone_id: _heuristic_threat_scores(metrics_by_drone[drone_id])
                        for drone_id in self.DRONES
                    }
        except Exception as exc:
            logger.warning("Batched FL inference failed (%s). Using heuristic scores.", exc)
            fl_fallback_reason = "fl_inference_failed"
            threats_by_drone = {
                drone_id: _heuristic_threat_scores(metrics_by_drone[drone_id])
                for drone_id in self.DRONES
            }

        rows = []
        for drone_id in self.DRONES:
            metrics = metrics_by_drone[drone_id]
            threat = threats_by_drone[drone_id]
            fallback_reasons = []
            if metrics.get("source") == "synthetic_fallback":
                fallback_reasons.append("live_sensor_unavailable")
            if threat.get("source") == "heuristic_fallback":
                fallback_reasons.append(fl_fallback_reason or "fl_inference_unavailable")

            path_scores = threat["path_scores"]
            path_latencies = [
                min(
                    1.0,
                    max(
                        0.0,
                        metrics["paths"][idx]["latency"] / MAX_LATENCY_MS[idx],
                    ),
                )
                for idx in range(3)
            ]
            path_losses = [
                min(1.0, max(0.0, metrics["paths"][idx]["packet_loss"]))
                for idx in range(3)
            ]

            # Step 3: RL decision
            rl_fallback_reason = "rl_model_unavailable"
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
            except Exception as exc:
                logger.warning("RL inference failed for %s (%s). Using greedy.", drone_id, exc)
                rl_fallback_reason = "rl_inference_failed"
                decision = self._greedy_decision(path_scores)

            if decision.get("decision_source") == "greedy_fallback":
                fallback_reasons.append(rl_fallback_reason)
            if decision.get("safety_override"):
                fallback_reasons.append("rl_safety_override")
            if decision.get("no_safe_route"):
                fallback_reasons.append("no_safe_route")

            requested_action_id = decision["action_id"]
            policy_action_id = int(
                decision.get("original_action_id", requested_action_id)
            )
            policy_path = _PATH_NAMES[policy_action_id]
            action_id = requested_action_id
            requested_path = decision["path_name"]
            threat_lvl = decision.get("threat_level", "UNKNOWN")

            # Graceful degradation: use last known good if stale
            current_ts = time.time()
            if self._last_good_decision[drone_id] and (current_ts - self._last_good_ts[drone_id] > 5.0):
                logger.warning("Decision cache stale (>5s) for %s. Using fallback.", drone_id)
                requested_path = "mesh"
                action_id = _PATH_NAMES.index(requested_path)
                requested_action_id = action_id
                fallback_reasons.append("decision_cache_stale")

            # Step 4: Push to SDN
            sdn_ok = False
            sdn_resp = None
            sdn_error = None
            installed_path = self._prev_path[drone_id]
            try:
                sdn_resp = await _push_to_sdn(client, requested_path, drone_id)
                sdn_ok = True
                reported_path = sdn_resp.get("installed_path", requested_path)
                installed_path = reported_path if reported_path in _PATH_NAMES else requested_path
                action_id = _PATH_NAMES.index(installed_path)
                self._last_good_decision[drone_id] = {
                    **decision,
                    "action_id": action_id,
                    "path_name": installed_path,
                }
                self._last_good_ts[drone_id] = current_ts
            except Exception as e:
                sdn_error = str(e)
                fallback_reasons.append("sdn_update_failed")
                logger.error("SDN push failed for %s: %s. Maintaining current path.", drone_id, e)
                if installed_path in _PATH_NAMES:
                    action_id = _PATH_NAMES.index(installed_path)

            # Legacy summary rows require a path even before the first successful
            # controller write. The canonical event keeps that state explicit by
            # leaving installed_path/action_id null when no route is known.
            effective_path = installed_path or requested_path
            effective_action_id = _PATH_NAMES.index(effective_path)

            # Step 5: Metrics
            previous_action = (
                _PATH_NAMES.index(self._prev_path[drone_id])
                if self._prev_path[drone_id] in _PATH_NAMES
                else None
            )
            reward_breakdown = self._compute_reward(
                path_scores,
                path_latencies,
                path_losses,
                effective_action_id,
                previous_action,
            )
            reward = reward_breakdown.total
            self._prev_rewards[drone_id] = reward
            recovery = self._recovery_ms(drone_id, effective_path)
            packet_loss = round(metrics["paths"][effective_action_id]["packet_loss"], 4)
            route_changed = sdn_ok and installed_path != self._prev_path[drone_id]

            event = DecisionEvent.model_validate({
                "event_id": f"{RUN_ID}:{self._step}:{drone_id}",
                "run_id": RUN_ID,
                "step": self._step,
                "timestamp": current_ts,
                "drone_id": drone_id,
                "mode": MODE,
                "telemetry": metrics,
                "inference": {
                    "path_scores": path_scores,
                    "confidence": threat["confidence"],
                    "attack_type": threat["attack_type"],
                    "source": threat.get("source", "fl_model"),
                    "model_id": (
                        self._fl_model_id
                        if threat.get("source") == "fl_model"
                        else None
                    ),
                },
                "decision": {
                    "source": decision.get("decision_source", "rl_model"),
                    "model_id": (
                        self._rl_model_id
                        if decision.get("decision_source") == "rl_model"
                        else None
                    ),
                    "policy_action_id": policy_action_id,
                    "policy_path": policy_path,
                    "requested_action_id": requested_action_id,
                    "requested_path": requested_path,
                    "installed_action_id": (
                        _PATH_NAMES.index(installed_path)
                        if installed_path is not None
                        else None
                    ),
                    "installed_path": installed_path,
                    "threat_level": threat_lvl,
                    "observation": decision.get("observation"),
                    "safety_override": bool(decision.get("safety_override", False)),
                    "safe_action_mask": decision.get(
                        "safe_action_mask", [True, True, True]
                    ),
                    "no_safe_route": bool(decision.get("no_safe_route", False)),
                    "constraint_reason": decision.get("constraint_reason"),
                    "safety_threshold": float(
                        decision.get("safety_threshold", 0.8)
                    ),
                    "all_unsafe_behavior": decision.get(
                        "all_unsafe_behavior", "least_risk_route"
                    ),
                    "route_changed": route_changed,
                },
                "sdn": {
                    "applied": sdn_ok,
                    "response": sdn_resp,
                    "error": sdn_error,
                },
                "outcome": {
                    "reward": reward,
                    "reward_definition": REWARD_DEFINITION,
                    "reward_components": reward_breakdown.as_dict(),
                    "path": effective_path,
                    "estimated": installed_path is None,
                    "recovery_ms": recovery,
                    "packet_loss": packet_loss,
                },
                "timing": {
                    "tick_elapsed_ms": (time.time() - t_start) * 1000.0,
                },
                "fallback_reasons": fallback_reasons,
            })

            row = {
                "timestamp":    round(current_ts, 3),
                "run_id":       RUN_ID,
                "step":         self._step,
                "drone_id":     drone_id,
                "action_id":    effective_action_id,
                "path_name":    effective_path,
                "threat_level": threat_lvl,
                "reward":       reward,
                "recovery_ms":  recovery,
                "packet_loss":  packet_loss,
                "fl_confidence": round(threat["confidence"], 4),
                "attack_type":  threat["attack_type"],
                "event_json": event.model_dump_json(),
            }
            rows.append(row)

            # Update path tracking
            if route_changed:
                self._prev_path[drone_id] = installed_path
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
