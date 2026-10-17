"""Online communication-threat detection and route-recovery control loop.

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
from dataclasses import dataclass
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
from typing import Literal, Optional

# Ensure project root is on sys.path when running as
# `python -m orchestrator.loop`
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import numpy as np
import torch
import yaml

from fleet.registry import active_drone_ids
from schemas.decision_event import DecisionEvent, TelemetrySnapshot
from sdn.route_contract import (
    HOLD_PATH,
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
    compute_secure_routing_reward,
    SECURE_REWARD_DEFINITION,
)
from rl.safety import constrain_route_action, resolve_safety_config

_BASE = Path(__file__).parent.parent
_MODE_CFG = yaml.safe_load((_BASE / "config" / "mode.yaml").read_text())
_FL_CFG = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
_SDN_CFG = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_SECURITY_CFG = yaml.safe_load(
    (_BASE / "config" / "security_config.yaml").read_text()
)
_SAFETY_CFG = _RL_CFG.get("safety", {})
_SAFETY_THRESHOLD, _ALL_UNSAFE_BEHAVIOR = resolve_safety_config(_SAFETY_CFG)

MODE = os.getenv("MODE", _MODE_CFG["mode"]).strip().lower()
if MODE not in _MODE_CFG or MODE not in {"simulation", "real"}:
    raise RuntimeError("MODE must be either 'simulation' or 'real'")
LOOP_INTERVAL = _MODE_CFG[MODE]["loop_interval_s"]
SDN_HOST = os.getenv("SDN_HOST", _SDN_CFG["controller"]["host"])
SDN_PORT = int(os.getenv("SDN_PORT", _SDN_CFG["controller"]["port"]))
SDN_URL = os.getenv("SDN_CONTROLLER_URL", f"http://{SDN_HOST}:{SDN_PORT}/sdn/route")
SDN_TIMEOUT = _SDN_CFG["controller"]["timeout_s"]
SDN_API_TOKEN = os.getenv("AJ_SDN_TOKEN", "antijam-development-sdn-token")
_EVIDENCE_CFG = _SECURITY_CFG.get("evidence", {})
SDN_EVIDENCE_URL = os.getenv(
    "AJ_SDN_EVIDENCE_URL",
    f"http://{SDN_HOST}:{SDN_PORT}"
    + str(_EVIDENCE_CFG.get("controller_endpoint", "/sdn/evidence/{drone_id}")),
)
if "{drone_id}" not in SDN_EVIDENCE_URL:
    raise RuntimeError("SDN evidence URL must contain the {drone_id} placeholder")
SDN_EVIDENCE_TIMEOUT = float(_EVIDENCE_CFG.get("request_timeout_s", SDN_TIMEOUT))
SENSOR_API_URL = os.getenv("AJ_SENSOR_API_URL", "http://localhost:9000").rstrip("/")
REAL_TELEMETRY_CACHE_TTL_S = float(
    _MODE_CFG.get("real", {}).get("telemetry_cache_ttl_s", 2.0)
)

# Experiment session
RUN_ID = str(uuid.uuid4())[:8]


@dataclass(frozen=True)
class TelemetryCollectionResult:
    drone_id: str
    status: Literal["fresh", "cached", "unavailable"]
    source: Literal["live", "cached_live", "unavailable"]
    metrics: Optional[dict]
    age_s: Optional[float]
    error: Optional[str]

    def status_payload(self) -> dict:
        return {
            "status": self.status,
            "source": self.source,
            "age_s": self.age_s,
            "error": self.error,
        }


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


def _resolve_db_path() -> Path:
    custom = os.getenv("DATABASE_PATH") or os.getenv("DATABASE_URL")
    if custom:
        if custom.startswith("sqlite:///"):
            custom = custom[len("sqlite:///"):]
        p = Path(custom)
        return p if p.is_absolute() else _BASE / p
    return _BASE / "experiments" / "experiment.db"


_CSV_PATH = _BASE / "experiments" / f"run_{RUN_ID}.csv"
_DB_PATH = _resolve_db_path()
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
_ACTION_NAMES_V3 = [*_PATH_NAMES, HOLD_PATH]


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operational_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT, timestamp REAL, step INTEGER, drone_id TEXT,
                    event_type TEXT, status TEXT, event_json TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_operational_drone_id_id "
                "ON operational_events (drone_id, id DESC)"
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


def _log_operational_events(events: list[dict]) -> None:
    if not events:
        return
    with closing(_connect_db()) as conn:
        with conn:
            conn.executemany(
                "INSERT INTO operational_events "
                "(run_id, timestamp, step, drone_id, event_type, status, event_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        event["run_id"], event["timestamp"], event["step"],
                        event["drone_id"], event["event_type"], event["status"],
                        json.dumps(event, sort_keys=True),
                    )
                    for event in events
                ],
            )


# ---------------------------------------------------------------------------
# FL Inference
# ---------------------------------------------------------------------------

def _load_fl_model(model_path: Path | None = None):
    """Load the global FL model. Returns None if not yet trained."""
    model_path = model_path or _BASE / _FL_CFG["paths"]["model_save"]
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

    grouped: dict[str, dict[str, list]] = {
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
    last_err: Optional[Exception] = None
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
    if last_err is not None:
        raise last_err
    raise RuntimeError("SDN push failed: maximum retries reached")


async def _push_containment(
    client: httpx.AsyncClient, drone_id: str, mode: str, reason: str
) -> dict:
    """Apply a cross-layer containment decision through the SDN controller."""
    response = await client.post(
        f"http://{SDN_HOST}:{SDN_PORT}/sdn/containment",
        json={"drone_id": drone_id, "mode": mode, "reason": reason},
        headers={"Authorization": f"Bearer {SDN_API_TOKEN}"},
        timeout=SDN_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("mode") != mode or payload.get("drone_id") != drone_id:
        raise RuntimeError("SDN containment response does not match its request")
    return payload


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self):
        self._running = True
        from runtime.model_deployment import DeploymentWatcher
        deployment_cfg = _MODE_CFG.get("deployment", {})
        self._deployment_watcher = DeploymentWatcher(
            _BASE,
            _BASE / deployment_cfg.get(
                "manifest_path", "models/deployment_manifest.json"
            ),
            poll_interval_s=float(deployment_cfg.get("poll_interval_s", 0.5)),
        )
        self._deployment_rollback = None
        self._deployment_just_activated = False
        self._fl_model = _load_fl_model()
        self._fl_model_id = _file_sha256(
            _BASE / _FL_CFG["paths"]["model_save"]
        ) if self._fl_model is not None else None
        self._step = 0

        self.DRONES = list(active_drone_ids())
        self._prev_path = {d: None for d in self.DRONES}
        self._prev_path_time = {d: None for d in self.DRONES}
        self._last_good_decision = {d: None for d in self.DRONES}
        self._last_good_ts = {d: 0.0 for d in self.DRONES}
        sequence_len = int(_FL_CFG["model"]["sequence_len"])
        self._sequence_len = sequence_len
        self._metric_history = {
            drone_id: deque(maxlen=sequence_len)
            for drone_id in self.DRONES
        }
        self._live_telemetry_cache: dict[str, tuple[float, dict]] = {}
        self._telemetry_collection_status: dict[str, dict] = {}

        # Keep independent stateful RL wrappers per drone. A single shared wrapper
        # would leak previous-action/reward state between drone decisions.
        self._rl_agents = {}
        preferred_contract = _RL_CFG.get("runtime", {}).get(
            "preferred_contract", "routing_state_v2"
        )
        v3_path = _BASE / _RL_CFG.get("runtime", {}).get(
            "v3_model_path", "models/rl_model_v3.zip"
        )
        v2_path = _BASE / _RL_CFG["paths"]["model_save"]
        if preferred_contract == "routing_state_v3" and v3_path.exists():
            rl_path, routing_contract_name = v3_path, "routing_state_v3"
        else:
            rl_path, routing_contract_name = v2_path, "routing_state_v2"
        self._rl_model_id = _file_sha256(rl_path)
        self._rl_path = rl_path
        self._routing_contract_name = routing_contract_name
        if rl_path.exists():
            from rl.agent import RLAgent
            try:
                for drone_id in self.DRONES:
                    self._rl_agents[drone_id] = RLAgent(
                        str(rl_path), routing_contract_name=routing_contract_name
                    )
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
        from fl.insider import InsiderTelemetryAnalyzer
        self._insider_analyzer = InsiderTelemetryAnalyzer(
            **_FL_CFG.get("insider_detection", {})
        )
        from security.network_detector import NetworkThreatAnalyzer
        self._network_analyzer = NetworkThreatAnalyzer(
            _SECURITY_CFG.get("network_detection", {})
        )
        self._containment_modes = {drone_id: "normal" for drone_id in self.DRONES}

        _init_experiment_store()
        logger.info("Orchestrator started | run_id=%s | mode=%s | interval=%.1fs",
                    RUN_ID, MODE, LOOP_INTERVAL)

    def _refresh_deployment(self) -> None:
        """Atomically swap a fully validated manifest generation at a step boundary."""
        candidate = self._deployment_watcher.candidate()
        if candidate is None:
            return
        generation = int(candidate["generation"])
        models = candidate["models"]
        try:
            next_fl_model = self._fl_model
            next_fl_id = self._fl_model_id
            if "fl" in models:
                if models["fl"]["contract"] != "threat_model_v2":
                    raise ValueError("unsupported deployed FL contract")
                next_fl_model = _load_fl_model(models["fl"]["resolved_path"])
                if next_fl_model is None:
                    raise ValueError("deployed FL checkpoint failed compatibility loading")
                with torch.no_grad():
                    next_fl_model(torch.zeros(
                        1,
                        int(_FL_CFG["model"]["sequence_len"]),
                        int(_FL_CFG["model"]["input_features"]),
                    ))
                next_fl_id = f"sha256:{models['fl']['checkpoint_sha256']}"

            next_agents = self._rl_agents
            next_rl_path = self._rl_path
            next_contract = self._routing_contract_name
            next_rl_id = self._rl_model_id
            if "routing" in models:
                next_contract = str(models["routing"]["contract"])
                if next_contract not in {"routing_state_v2", "routing_state_v3"}:
                    raise ValueError("unsupported deployed routing contract")
                next_rl_path = models["routing"]["resolved_path"]
                from rl.agent import RLAgent
                next_agents = {
                    drone_id: RLAgent(
                        str(next_rl_path), routing_contract_name=next_contract
                    )
                    for drone_id in self.DRONES
                }
                next_rl_id = f"sha256:{models['routing']['checkpoint_sha256']}"
            self._deployment_rollback = (
                self._fl_model, self._fl_model_id, self._rl_agents,
                self._rl_path, self._routing_contract_name, self._rl_model_id,
                self._deployment_watcher.active_generation,
            )
            self._fl_model, self._fl_model_id = next_fl_model, next_fl_id
            self._rl_agents = next_agents
            self._rl_path, self._routing_contract_name = next_rl_path, next_contract
            self._rl_model_id = next_rl_id
            self._deployment_watcher.activate(generation)
            self._deployment_just_activated = True
            logger.info("Activated model deployment generation %d", generation)
        except Exception as exc:
            self._deployment_watcher.reject(generation, exc)
            logger.error("Rejected model deployment generation %d: %s", generation, exc)

    def _rollback_deployment(self, reason: str) -> None:
        if not self._deployment_just_activated or self._deployment_rollback is None:
            return
        failed_generation = self._deployment_watcher.active_generation
        (
            self._fl_model, self._fl_model_id, self._rl_agents,
            self._rl_path, self._routing_contract_name, self._rl_model_id,
            previous_generation,
        ) = self._deployment_rollback
        self._deployment_watcher.active_generation = previous_generation
        self._deployment_watcher.reject(failed_generation, reason)
        self._deployment_just_activated = False
        logger.error(
            "Rolled back deployment generation %d after first-use failure: %s",
            failed_generation,
            reason,
        )

    def _sync_fleet(self) -> None:
        """Discover newly enrolled drones and initialize isolated runtime state."""
        current = list(active_drone_ids())
        added = [drone_id for drone_id in current if drone_id not in self.DRONES]
        if not added:
            self.DRONES = current
            return

        for drone_id in added:
            self._prev_path[drone_id] = None
            self._prev_path_time[drone_id] = None
            self._last_good_decision[drone_id] = None
            self._last_good_ts[drone_id] = 0.0
            self._metric_history[drone_id] = deque(maxlen=self._sequence_len)
            self._prev_rewards[drone_id] = 0.0
            self._containment_modes[drone_id] = "normal"
            if self._rl_path.exists():
                try:
                    from rl.agent import RLAgent

                    self._rl_agents[drone_id] = RLAgent(
                        str(self._rl_path),
                        routing_contract_name=self._routing_contract_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "RL wrapper initialization failed for newly enrolled %s (%s); "
                        "using greedy fallback.",
                        drone_id,
                        exc,
                    )
        self.DRONES = current
        logger.info("Discovered enrolled drones without restart: %s", ", ".join(added))

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
        if action == 3 or previous_action == 3:
            return compute_secure_routing_reward(
                action,
                path_scores,
                path_latencies,
                path_losses,
                previous_action=previous_action,
            )
        return compute_routing_reward(
            action,
            path_scores,
            path_latencies,
            path_losses,
            previous_action=previous_action,
        )

    @staticmethod
    def _fl_trust_context() -> dict[str, dict]:
        """Read the latest server-authenticated reputation snapshot."""
        path = _BASE / _FL_CFG.get("paths", {}).get(
            "metrics_json", "results/fl_metrics_snapshot.json"
        )
        if not path.exists():
            return {}
        try:
            latest = json.loads(path.read_text(encoding="utf-8")).get("latest", {})
            rows = latest.get("client_security", [])
            return {
                str(row["client_id"]): row
                for row in rows
                if isinstance(row, dict) and row.get("client_id")
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _recovery_ms(self, drone_id: str, new_path: str) -> Optional[float]:
        """Return recovery time in ms if path switched, else None."""
        prev = self._prev_path[drone_id]
        if prev is not None and new_path != prev:
            prev_t = self._prev_path_time[drone_id]
            if prev_t is not None:
                return round((time.time() - prev_t) * 1000, 1)
        return None

    async def _collect_metrics(self, client: httpx.AsyncClient) -> dict[str, dict]:
        if not hasattr(self, "_live_telemetry_cache"):
            self._live_telemetry_cache = {}
        if not hasattr(self, "_telemetry_collection_status"):
            self._telemetry_collection_status = {}
        if MODE == "simulation":
            from simulation.generator import generate_swarm_metrics
            metrics = generate_swarm_metrics()
            self._telemetry_collection_status = {
                drone_id: {
                    "status": "fresh",
                    "source": "synthetic",
                    "age_s": 0.0,
                    "error": None,
                }
                for drone_id in metrics
            }
            return metrics

        async def fetch_one(drone_id: str) -> TelemetryCollectionResult:
            try:
                response = await client.get(
                    f"{SENSOR_API_URL}/metrics",
                    params={"drone_id": drone_id},
                    timeout=1.0,
                )
                response.raise_for_status()
                metrics = response.json()
                metrics["source"] = "live"
                reported_evidence = metrics.pop("security_evidence", None)
                validated = TelemetrySnapshot.model_validate(metrics)
                if validated.drone_id != drone_id:
                    raise ValueError("sensor response drone_id does not match request")
                payload = validated.model_dump()
                controller_evidence = None
                try:
                    evidence_response = await client.get(
                        SDN_EVIDENCE_URL.format(drone_id=drone_id),
                        headers={"Authorization": f"Bearer {SDN_API_TOKEN}"},
                        timeout=SDN_EVIDENCE_TIMEOUT,
                    )
                    evidence_response.raise_for_status()
                    controller_evidence = evidence_response.json()
                except Exception as evidence_exc:
                    logger.warning(
                        "Controller evidence unavailable for %s (%s).",
                        drone_id,
                        evidence_exc,
                    )
                if controller_evidence is not None:
                    from security.adapters import merge_security_evidence
                    payload["security_evidence"] = merge_security_evidence(
                        reported_evidence,
                        controller_evidence,
                        category=str(controller_evidence.get("source", "unknown")),
                        observer_id=f"sdn:{SDN_HOST}:{SDN_PORT}",
                        independent=bool(controller_evidence.get("independent", False)),
                        max_join_skew_s=float(
                            _SECURITY_CFG.get("evidence", {}).get("max_age_s", 3.0)
                        ),
                        max_window_alignment_s=float(
                            _SECURITY_CFG.get("evidence", {}).get(
                                "max_window_alignment_s", 0.5
                            )
                        ),
                    )
                collected_at = time.time()
                payload["source_collected_at"] = collected_at
                payload["source_age_s"] = 0.0
                self._live_telemetry_cache[drone_id] = (collected_at, payload)
                return TelemetryCollectionResult(
                    drone_id, "fresh", "live", payload, 0.0, None
                )
            except Exception as exc:
                cached = self._live_telemetry_cache.get(drone_id)
                if cached is not None:
                    collected_at, cached_metrics = cached
                    age = max(0.0, time.time() - collected_at)
                    if age <= REAL_TELEMETRY_CACHE_TTL_S:
                        metrics = dict(cached_metrics)
                        metrics["source"] = "cached_live"
                        metrics["source_collected_at"] = collected_at
                        metrics["source_age_s"] = age
                        logger.warning(
                            "Live metrics unavailable for %s (%s). Using %.3fs-old "
                            "validated live cache.", drone_id, exc, age,
                        )
                        return TelemetryCollectionResult(
                            drone_id, "cached", "cached_live", metrics, age, str(exc)
                        )
                logger.error(
                    "Live metrics unavailable for %s (%s). Failing closed.",
                    drone_id,
                    exc,
                )
                return TelemetryCollectionResult(
                    drone_id, "unavailable", "unavailable", None, None, str(exc)
                )

        pairs = await asyncio.gather(*(fetch_one(drone_id) for drone_id in self.DRONES))
        self._telemetry_collection_status = {
            result.drone_id: result.status_payload() for result in pairs
        }
        return {
            result.drone_id: result.metrics for result in pairs
            if result.metrics is not None
        }

    async def _fail_closed_unavailable_drones(
        self, client: httpx.AsyncClient, unavailable: list[str]
    ) -> None:
        events = []
        for drone_id in unavailable:
            now = time.time()
            applied = False
            error = None
            response = None
            try:
                response = await _push_to_sdn(client, HOLD_PATH, drone_id)
                applied = True
                self._prev_path[drone_id] = HOLD_PATH
                self._prev_path_time[drone_id] = now
            except Exception as exc:
                error = str(exc)
                logger.error("Fail-closed HOLD failed for %s: %s", drone_id, exc)
            events.append({
                "schema_version": "operational_safety_event_v1",
                "run_id": RUN_ID,
                "timestamp": now,
                "step": self._step,
                "drone_id": drone_id,
                "event_type": "telemetry_unavailable",
                "status": "hold_applied" if applied else "hold_failed",
                "telemetry": self._telemetry_collection_status.get(drone_id, {}),
                "decision": {"network_action": "hold", "requested_path": HOLD_PATH},
                "sdn": {"applied": applied, "response": response, "error": error},
            })
        await asyncio.to_thread(_log_operational_events, events)

    async def _run_step(self, client: httpx.AsyncClient) -> None:
        self._sync_fleet()
        if hasattr(self, "_deployment_watcher"):
            self._refresh_deployment()
        self._step += 1
        t_start = time.time()
        metrics_by_drone = await self._collect_metrics(client)
        unavailable = [
            drone_id for drone_id in self.DRONES if drone_id not in metrics_by_drone
        ]
        if unavailable:
            await self._fail_closed_unavailable_drones(client, unavailable)
        processing_drones = [
            drone_id for drone_id in self.DRONES if drone_id in metrics_by_drone
        ]
        if not processing_drones:
            return
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
                        for drone_id in processing_drones
                    }
        except Exception as exc:
            logger.warning("Batched FL inference failed (%s). Using heuristic scores.", exc)
            self._rollback_deployment(f"fl_inference_failed: {exc}")
            fl_fallback_reason = "fl_inference_failed"
            threats_by_drone = {
                drone_id: _heuristic_threat_scores(metrics_by_drone[drone_id])
                for drone_id in processing_drones
            }

        rows = []
        trust_context = self._fl_trust_context()
        if not hasattr(self, "_insider_analyzer"):
            from fl.insider import InsiderTelemetryAnalyzer
            self._insider_analyzer = InsiderTelemetryAnalyzer(
                **_FL_CFG.get("insider_detection", {})
            )
        if not hasattr(self, "_containment_modes"):
            self._containment_modes = {drone: "normal" for drone in self.DRONES}
        for drone_id in processing_drones:
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

            evidence = metrics.get("security_evidence")
            if not hasattr(self, "_network_analyzer"):
                from security.network_detector import NetworkThreatAnalyzer
                self._network_analyzer = NetworkThreatAnalyzer(
                    _SECURITY_CFG.get("network_detection", {})
                )
            network_analysis = self._network_analyzer.analyze(
                drone_id,
                evidence,
                max_path_latency_ms=max(
                    float(path["latency"]) for path in metrics["paths"]
                ),
                max_evidence_age_s=float(
                    _SECURITY_CFG.get("evidence", {}).get("max_age_s", 3.0)
                ),
                require_independent=MODE == "real",
            )
            # Both traffic analyzers consume the same joined evidence. An
            # unavailable real controller observation must not be converted
            # into a valid insider risk merely because its counters parse.
            insider_analysis = (
                self._insider_analyzer.analyze(drone_id, evidence)
                if isinstance(evidence, dict)
                and network_analysis.status != "UNAVAILABLE"
                else None
            )
            trust_row = trust_context.get(drone_id, {})
            client_trust = float(trust_row.get("trust_score", 1.0))
            insider_risk = (
                insider_analysis.risk_score if insider_analysis is not None else 0.0
            )
            evidence_freshness = (
                insider_analysis.evidence_freshness if insider_analysis is not None else 0.0
            )
            from sdn.containment import decide_containment
            containment = decide_containment(
                drone_id,
                trust_score=client_trust,
                insider_risk=insider_risk,
                network_risk=(
                    network_analysis.risk_score
                    if network_analysis.status != "UNAVAILABLE" else None
                ),
                network_response_hint=network_analysis.response_hint,
                **_SDN_CFG.get("containment", {}),
            )
            containment_score = max(
                insider_risk,
                network_analysis.risk_score or 0.0,
                1.0 - client_trust,
            )

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
                            client_trust=client_trust,
                            insider_risk=insider_risk,
                            containment_score=containment_score,
                            evidence_freshness=evidence_freshness,
                        )
                        decision = await loop.run_in_executor(
                            None, predict
                        )
                    else:
                        decision = self._greedy_decision(path_scores)
            except Exception as exc:
                logger.warning("RL inference failed for %s (%s). Using greedy.", drone_id, exc)
                self._rollback_deployment(f"rl_inference_failed: {exc}")
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
            action_names = (
                _ACTION_NAMES_V3
                if decision.get("routing_contract") == "routing_state_v3"
                else _PATH_NAMES
            )
            policy_path = action_names[policy_action_id]
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

            # Step 4a: Enforce containment before changing forwarding state.
            containment_response = None
            if (
                (insider_analysis is not None or network_analysis.status != "UNAVAILABLE")
                and self._containment_modes.get(drone_id) != containment.mode.value
            ):
                try:
                    containment_response = await _push_containment(
                        client, drone_id, containment.mode.value, containment.reason
                    )
                    self._containment_modes[drone_id] = containment.mode.value
                except Exception as exc:
                    fallback_reasons.append("containment_update_failed")
                    logger.error("Containment push failed for %s: %s", drone_id, exc)
                    # Failure to install a restrictive rule fails closed through
                    # the independently executable HOLD action.
                    if containment.mode.value != "normal":
                        requested_path = HOLD_PATH
                        requested_action_id = 3
                        action_id = 3

            # Step 4b: Push route/HOLD command to SDN
            sdn_ok = False
            sdn_resp = None
            sdn_error = None
            installed_path = self._prev_path[drone_id]
            try:
                sdn_resp = await _push_to_sdn(client, requested_path, drone_id)
                sdn_ok = True
                reported_path = sdn_resp.get("installed_path", requested_path)
                installed_path = reported_path if reported_path in _ACTION_NAMES_V3 else requested_path
                action_id = _ACTION_NAMES_V3.index(installed_path)
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
                if installed_path in _ACTION_NAMES_V3:
                    action_id = _ACTION_NAMES_V3.index(installed_path)

            # Legacy summary rows require a path even before the first successful
            # controller write. The canonical event keeps that state explicit by
            # leaving installed_path/action_id null when no route is known.
            effective_path = installed_path or requested_path
            effective_action_id = _ACTION_NAMES_V3.index(effective_path)

            # Step 5: Metrics
            previous_action = (
                _ACTION_NAMES_V3.index(self._prev_path[drone_id])
                if self._prev_path[drone_id] in _ACTION_NAMES_V3
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
            packet_loss = (
                1.0
                if effective_action_id == 3
                else round(metrics["paths"][effective_action_id]["packet_loss"], 4)
            )
            route_changed = sdn_ok and installed_path != self._prev_path[drone_id]

            event = DecisionEvent.model_validate({
                "schema_version": (
                    "3.1"
                    if insider_analysis is not None
                    or network_analysis.status != "UNAVAILABLE"
                    or decision.get("routing_contract") == "routing_state_v3"
                    or effective_path == HOLD_PATH
                    else "2.1"
                ),
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
                    "model_generation": (
                        self._deployment_watcher.active_generation
                        if hasattr(self, "_deployment_watcher") else 0
                    ),
                },
                "insider_analysis": (
                    insider_analysis.to_dict() if insider_analysis is not None else None
                ),
                "network_security_analysis": network_analysis.to_dict(),
                "containment": (
                    containment.to_dict()
                    if insider_analysis is not None
                    or network_analysis.status != "UNAVAILABLE"
                    else None
                ),
                "decision": {
                    "source": decision.get("decision_source", "rl_model"),
                    "model_id": (
                        self._rl_model_id
                        if decision.get("decision_source") == "rl_model"
                        else None
                    ),
                    "model_generation": (
                        self._deployment_watcher.active_generation
                        if hasattr(self, "_deployment_watcher") else 0
                    ),
                    "policy_action_id": policy_action_id,
                    "policy_path": policy_path,
                    "requested_action_id": requested_action_id,
                    "requested_path": requested_path,
                    "installed_action_id": (
                        _ACTION_NAMES_V3.index(installed_path)
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
                    "network_action": "hold" if requested_path == HOLD_PATH else "forward",
                    "routing_contract": decision.get(
                        "routing_contract", "routing_state_v2"
                    ),
                    "route_changed": route_changed,
                },
                "sdn": {
                    "applied": sdn_ok,
                    "response": {
                        "route": sdn_resp,
                        "containment": containment_response,
                    },
                    "error": sdn_error,
                },
                "outcome": {
                    "reward": reward,
                    "reward_definition": (
                        SECURE_REWARD_DEFINITION
                        if effective_path == HOLD_PATH
                        else REWARD_DEFINITION
                    ),
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
        self._deployment_just_activated = False

        elapsed_ms = round((time.time() - t_start) * 1000, 1)
        logger.info("step=%d | processed %d drones | elapsed=%s ms", self._step, len(self.DRONES), elapsed_ms)

    async def run(self):
        """Main async loop."""
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    await self._run_step(client)
                except Exception:
                    logger.exception("Unexpected error in step %d", self._step)
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
