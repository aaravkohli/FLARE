"""
api/server.py — Anti-Jamming Drone System API

Endpoints:
  POST /auth/token       — login, returns JWT token
  POST /predict          — accepts path_scores, returns best path (protected)
  GET  /health           — liveness check (public)
  GET  /ready            — SDN data-plane readiness (public)
  GET  /ready/history    — recent SDN readiness transitions (protected)
  GET  /metrics/live     — latest raw RF metrics snapshot (protected)
  GET  /metrics/history  — last N decisions from SQLite for charting (protected)
  POST /jam              — trigger jamming simulation (protected)
  GET  /stream           — protected SSE stream of orchestrator decisions
  WS   /ws               — authenticated WebSocket telemetry stream
"""

import asyncio
from collections import deque
import json
import logging
import math
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Set, Literal

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
import httpx
import torch
import uvicorn
import yaml
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
    status,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from schemas.decision_event import DecisionEvent
from api.readiness_history import ReadinessHistory
from rl.safety import constrain_route_action, resolve_safety_config

_BASE = Path(__file__).parent.parent
_RL_CFG = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
_SDN_CFG = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_SAFETY_THRESHOLD, _ALL_UNSAFE_BEHAVIOR = resolve_safety_config(
    _RL_CFG.get("safety", {})
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [API] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth Configuration
# ---------------------------------------------------------------------------

APP_ENV = os.getenv("AJ_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}
_DEVELOPMENT_SECRET = "antijam-development-secret-do-not-use-in-production"
SECRET_KEY = os.getenv("AJ_SECRET_KEY", _DEVELOPMENT_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8-hour sessions

if IS_PRODUCTION and (
    SECRET_KEY == _DEVELOPMENT_SECRET or len(SECRET_KEY) < 32
):
    raise RuntimeError(
        "AJ_SECRET_KEY must be set to a unique value of at least 32 characters "
        "when AJ_ENV=production"
    )

ADMIN_USERNAME = os.getenv("AJ_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("AJ_ADMIN_PASSWORD", "antijam2026")
if IS_PRODUCTION and "AJ_ADMIN_PASSWORD" not in os.environ:
    raise RuntimeError("AJ_ADMIN_PASSWORD must be set when AJ_ENV=production")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# Simple in-memory user store — production deployments should use a persistent
# identity provider. The local-development default is admin/antijam2026.
_USERS: dict = {}  # filled at module load below
_login_failures: Dict[str, List[float]] = {}
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_FAILURES = 5
_DUMMY_PASSWORD_HASH = pwd_context.hash("invalid-user-password-placeholder")


def _build_users():
    """Build the configured operator account without storing plaintext hashes."""
    return {
        ADMIN_USERNAME: {
            "username": ADMIN_USERNAME,
            "hashed_password": pwd_context.hash(ADMIN_PASSWORD),
            "role": "admin",
        }
    }

_USERS = _build_users()


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _authenticate_token(token: str) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = _USERS.get(username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    return _authenticate_token(token)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

VALID_DRONE_IDS = {"drone_1", "drone_2", "drone_3"}
VALID_JAM_DRONE_IDS = VALID_DRONE_IDS | {"all"}


class Token(BaseModel):
    access_token: str
    token_type: str
    username: str


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_scores: List[float]
    drone_id: str = "drone_1"
    prev_reward: float = 0.0

    @field_validator("path_scores")
    @classmethod
    def validate_scores(cls, v):
        if len(v) != 3:
            raise ValueError("path_scores must have exactly 3 values (direct, satellite, mesh)")
        for s in v:
            if not math.isfinite(s) or not (0.0 <= s <= 1.0):
                raise ValueError(f"All scores must be in [0, 1], got {s}")
        return v

    @field_validator("drone_id")
    @classmethod
    def validate_drone_id(cls, value):
        if value not in VALID_DRONE_IDS:
            raise ValueError(f"drone_id must be one of {sorted(VALID_DRONE_IDS)}")
        return value

    @field_validator("prev_reward")
    @classmethod
    def validate_prev_reward(cls, value):
        if not math.isfinite(value):
            raise ValueError("prev_reward must be finite")
        return value


class PredictResponse(BaseModel):
    action_id: int
    path_name: str
    threat_level: str
    original_action_id: int
    safety_override: bool
    safe_action_mask: List[bool]
    no_safe_route: bool
    constraint_reason: Optional[str] = None
    safety_threshold: float
    all_unsafe_behavior: str
    fl_confidence: Optional[float] = None
    attack_type: Optional[str] = None
    timestamp: float


class HealthResponse(BaseModel):
    status: str
    rl_loaded: bool
    fl_loaded: bool
    uptime_s: float
    mode: str
    sdn_controller: str
    version: str = "2.0.0"


class SDNReadiness(BaseModel):
    mode: str
    ready: bool
    status: str
    connected_switches: int = 0
    expected_switches: int = 0
    available_paths: Dict[str, List[str]] = Field(default_factory=dict)
    error: Optional[str] = None


class ReadinessTransition(BaseModel):
    timestamp: float
    severity: Literal["info", "warning", "critical"]
    message: str
    mode: str
    ready: bool
    status: str
    connected_switches: int
    expected_switches: int
    available_paths: Dict[str, List[str]]
    unavailable_paths: Dict[str, List[str]]
    error: Optional[str] = None


class ReadinessResponse(BaseModel):
    status: str
    ready: bool
    sdn: SDNReadiness
    alert: ReadinessTransition


class ReadinessHistoryResponse(BaseModel):
    events: List[ReadinessTransition]
    count: int
    capacity: int


class JamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: List[str] = Field(default_factory=list, max_length=3)
    duration: float = Field(default=10.0, ge=0.0, le=3600.0)
    drone_id: str = "drone_1"
    profile: str = "spot"

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, paths):
        from simulation.jammer import VALID_PATHS
        if len(paths) != len(set(paths)):
            raise ValueError("paths must not contain duplicates")
        invalid = set(paths) - VALID_PATHS
        if invalid:
            raise ValueError(f"Unknown paths: {sorted(invalid)}")
        return paths

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, value):
        if not math.isfinite(value):
            raise ValueError("duration must be finite")
        return value

    @field_validator("drone_id")
    @classmethod
    def validate_jam_drone_id(cls, value):
        if value not in VALID_JAM_DRONE_IDS:
            raise ValueError(f"drone_id must be one of {sorted(VALID_JAM_DRONE_IDS)}")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value):
        from simulation.jammer import VALID_PROFILES
        if value not in VALID_PROFILES:
            raise ValueError(f"profile must be one of {sorted(VALID_PROFILES)}")
        return value

    @model_validator(mode="after")
    def validate_profile_paths(self):
        pathless_profiles = {
            "none", "barrage", "sweep", "gps_spoofing", "sybil",
            "model_poisoning", "data_poisoning", "backdoor",
        }
        if self.profile not in pathless_profiles and not self.paths:
            raise ValueError(f"profile '{self.profile}' requires at least one target path")
        if self.profile == "none" and self.paths:
            raise ValueError("profile 'none' must not include target paths")
        return self


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

_rl_agents: Dict[str, Any] = {}
_fl_model = None
_start_time = time.time()
_last_jam_time: Dict[str, float] = {}  # drone_id -> timestamp of last jam request
_ws_clients: List[WebSocket] = []  # Connected WebSocket clients
_compromised_drones: Set[str] = set()  # Set of compromised drones (Byzantine test)
_FL_SEQUENCE_LEN = int(
    yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())["model"][
        "sequence_len"
    ]
)
_xai_metric_history = {
    drone_id: {
        path_name: deque(maxlen=_FL_SEQUENCE_LEN)
        for path_name in ("direct", "satellite", "mesh")
    }
    for drone_id in VALID_DRONE_IDS
}
_xai_last_event_ids: Dict[str, str] = {}

try:
    _READINESS_HISTORY_LIMIT = int(os.getenv("AJ_READINESS_HISTORY_LIMIT", "100"))
except ValueError as exc:
    raise RuntimeError("AJ_READINESS_HISTORY_LIMIT must be an integer") from exc
_READINESS_HISTORY_LIMIT = max(10, min(_READINESS_HISTORY_LIMIT, 1000))
_readiness_history = ReadinessHistory(
    max_entries=_READINESS_HISTORY_LIMIT,
    drone_ids=VALID_DRONE_IDS,
    route_names=("direct", "satellite", "mesh"),
)

_RUN_SUMMARY_COLUMNS = (
    "id, run_id, timestamp, step, drone_id, action_id, path_name, "
    "threat_level, reward, recovery_ms, packet_loss, fl_confidence, attack_type"
)


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _latest_decision_events() -> Dict[str, dict]:
    """Return the latest valid canonical event for each drone, if available."""
    db_path = _BASE / "experiments" / "experiment.db"
    if not db_path.exists():
        return {}

    try:
        async with aiosqlite.connect(db_path) as db:
            if "event_json" not in await _table_columns(db, "runs"):
                return {}
            cursor = await db.execute(
                "SELECT event_json FROM runs "
                "WHERE event_json IS NOT NULL ORDER BY id DESC LIMIT 300"
            )
            rows = await cursor.fetchall()
    except Exception as exc:
        logger.debug("Canonical event DB read error: %s", exc)
        return {}

    events: Dict[str, dict] = {}
    for (raw_event,) in rows:
        try:
            event = DecisionEvent.model_validate_json(raw_event)
        except Exception as exc:
            logger.warning("Ignoring invalid persisted decision event: %s", exc)
            continue
        if event.drone_id not in events:
            events[event.drone_id] = event.model_dump(mode="json")
        if len(events) == len(VALID_DRONE_IDS):
            break
    return events


async def _latest_run_summaries() -> Dict[str, dict]:
    """Read legacy summary columns without leaking the large event_json field."""
    db_path = _BASE / "experiments" / "experiment.db"
    if not db_path.exists():
        return {}
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT {_RUN_SUMMARY_COLUMNS} FROM runs WHERE id IN "
                "(SELECT MAX(id) FROM runs GROUP BY drone_id)"
            )
            rows = await cursor.fetchall()
    except Exception as exc:
        logger.debug("Experiment summary DB read error: %s", exc)
        return {}
    return {row["drone_id"]: dict(row) for row in rows}


def _decision_summary_from_event(event: dict) -> dict:
    decision = event["decision"]
    outcome = event["outcome"]
    return {
        "path_name": decision["installed_path"] or decision["requested_path"],
        "policy_path": decision["policy_path"],
        "requested_path": decision["requested_path"],
        "threat_level": decision["threat_level"],
        "reward": outcome["reward"],
        "step": event["step"],
        "source": "canonical_event",
        "event_id": event["event_id"],
        "sdn_applied": event["sdn"]["applied"],
        "fallback_reasons": event["fallback_reasons"],
        "safety_override": decision.get("safety_override", False),
        "safe_action_mask": decision.get("safe_action_mask", [True, True, True]),
        "no_safe_route": decision.get("no_safe_route", False),
        "constraint_reason": decision.get("constraint_reason"),
        "safety_threshold": decision.get("safety_threshold", 0.8),
        "all_unsafe_behavior": decision.get(
            "all_unsafe_behavior", "least_risk_route"
        ),
    }


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _rl_agents, _fl_model

    # Always rebuild the runtime wrappers from the currently validated artifact.
    # This prevents an in-process lifespan restart from retaining agents loaded
    # from a checkpoint that has since become missing or incompatible.
    _rl_agents = {}
    _fl_model = None
    _xai_last_event_ids.clear()
    for drone_history in _xai_metric_history.values():
        for path_history in drone_history.values():
            path_history.clear()
    rl_model_path = _BASE / "models" / "rl_model.zip"
    if rl_model_path.exists():
        try:
            from rl.agent import RLAgent
            _rl_agents = {
                drone_id: RLAgent(str(rl_model_path))
                for drone_id in sorted(VALID_DRONE_IDS)
            }
            logger.info("RL agents loaded for %d drones.", len(_rl_agents))
        except Exception as e:
            logger.warning("Failed to load RL agent: %s. Using greedy fallback.", e)
    else:
        logger.warning("rl_model.zip not found. Run `python rl/train.py` first.")

    fl_model_path = _BASE / "models" / "fl_model.pth"
    if fl_model_path.exists():
        try:
            from fl.checkpoint import validate_fl_checkpoint_metadata
            from fl.model import build_model
            fl_cfg = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
            validate_fl_checkpoint_metadata(
                fl_model_path,
                model_config=fl_cfg["model"],
                data_config=fl_cfg.get("data", {}),
            )
            _fl_model = build_model(fl_cfg["model"])
            _fl_model.load_state_dict(torch.load(fl_model_path, map_location="cpu"))
            _fl_model.eval()
            logger.info("FL model loaded.")
        except Exception as e:
            logger.warning("Failed to load FL model: %s", e)

    # Start background telemetry broadcaster
    broadcaster_task = asyncio.create_task(telemetry_broadcaster())
    logger.info("Background telemetry broadcaster task started.")

    yield

    broadcaster_task.cancel()
    try:
        await broadcaster_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutting down API server.")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Anti-Jamming Drone System API",
    description="Production-ready API for autonomous anti-jamming FL+RL+SDN pipeline",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

_default_cors_origins = (
    "" if IS_PRODUCTION else "http://localhost:5173,http://127.0.0.1:5173"
)
_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "AJ_CORS_ORIGINS",
        _default_cors_origins,
    ).split(",")
    if origin.strip()
]
if IS_PRODUCTION and not _cors_origins:
    raise RuntimeError("AJ_CORS_ORIGINS must contain at least one trusted origin in production")
if "*" in _cors_origins:
    raise RuntimeError("AJ_CORS_ORIGINS must list explicit trusted origins; wildcard CORS is disabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Auth Endpoints
# ---------------------------------------------------------------------------


@app.post("/auth/token", response_model=Token, tags=["Auth"])
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """Authenticate and retrieve a JWT bearer token."""
    client_id = request.client.host if request.client else "unknown"
    now = time.monotonic()
    failures = [
        failed_at
        for failed_at in _login_failures.get(client_id, [])
        if now - failed_at < LOGIN_WINDOW_SECONDS
    ]
    if len(failures) >= LOGIN_MAX_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts; try again later",
            headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)},
        )

    user = _USERS.get(form_data.username)
    password_hash = user["hashed_password"] if user else _DUMMY_PASSWORD_HASH
    password_valid = _verify_password(form_data.password, password_hash)
    if not user or not password_valid:
        failures.append(now)
        _login_failures[client_id] = failures
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _login_failures.pop(client_id, None)
    token = _create_access_token(
        data={"sub": user["username"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=token, token_type="bearer", username=user["username"])


# ---------------------------------------------------------------------------
# Core Endpoints
# ---------------------------------------------------------------------------


def _greedy_predict(path_scores: List[float]) -> dict:
    policy_action = int(min(range(3), key=lambda i: path_scores[i]))
    constrained = constrain_route_action(
        policy_action,
        path_scores,
        threat_threshold=_SAFETY_THRESHOLD,
    )
    action = constrained.action_id
    paths = ["direct", "satellite", "mesh"]
    max_score = max(path_scores)
    level = "HIGH" if max_score >= 0.7 else ("MEDIUM" if max_score >= 0.4 else "LOW")
    return {
        "action_id": action,
        "path_name": paths[action],
        "threat_level": level,
        "original_action_id": policy_action,
        "safety_override": constrained.safety_override,
        "safe_action_mask": list(constrained.safe_action_mask),
        "no_safe_route": constrained.no_safe_route,
        "constraint_reason": constrained.constraint_reason,
        "safety_threshold": constrained.threat_threshold,
        "all_unsafe_behavior": constrained.all_unsafe_behavior,
    }


def _runtime_mode_and_sdn_controller() -> tuple[str, str]:
    configured_mode = os.getenv("MODE")
    if configured_mode is None:
        mode_path = _BASE / "config" / "mode.yaml"
        mode_config = yaml.safe_load(mode_path.read_text()) if mode_path.exists() else {}
        configured_mode = mode_config.get("mode", "unknown")
    runtime_mode = configured_mode.strip().lower()
    sdn_controller = os.getenv(
        "AJ_SDN_MODE",
        "mock" if runtime_mode == "simulation" else "ryu",
    ).strip().lower()
    return runtime_mode, sdn_controller


async def _fetch_sdn_readiness() -> SDNReadiness:
    """Fetch and normalize the active controller's readiness contract."""
    _, sdn_controller = _runtime_mode_and_sdn_controller()
    controller_cfg = _SDN_CFG.get("controller", {})
    host = os.getenv("SDN_HOST", str(controller_cfg.get("host", "127.0.0.1")))
    port = int(os.getenv("SDN_PORT", str(controller_cfg.get("port", 8080))))
    timeout_s = float(
        os.getenv("AJ_SDN_READINESS_TIMEOUT", str(controller_cfg.get("timeout_s", 1.0)))
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            controller_response = await client.get(f"http://{host}:{port}/ready")
        payload = controller_response.json()
        if not isinstance(payload, dict):
            raise ValueError("controller returned a non-object response")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("SDN readiness check failed: %s", exc)
        return SDNReadiness(
            mode=sdn_controller,
            ready=False,
            status="unreachable",
            error="SDN readiness endpoint is unavailable",
        )

    topology = payload.get("topology")
    if not isinstance(topology, dict):
        topology = {}
    connected = topology.get("connected_switches", [])
    expected = topology.get("expected_switches", [])
    available_paths = topology.get("available_paths", {})
    if not isinstance(available_paths, dict):
        available_paths = {}
    routable_paths = {"direct", "satellite", "mesh"}

    controller_ready = bool(topology.get("ready", payload.get("ready", False)))
    ready = (
        controller_response.status_code == status.HTTP_200_OK
        and controller_ready
    )
    return SDNReadiness(
        mode=str(payload.get("mode", sdn_controller)),
        ready=ready,
        status="ready" if ready else str(payload.get("status", "not_ready")),
        connected_switches=len(connected) if isinstance(connected, list) else 0,
        expected_switches=len(expected) if isinstance(expected, list) else 0,
        available_paths={
            str(drone_id): [
                str(path) for path in paths if str(path) in routable_paths
            ]
            for drone_id, paths in available_paths.items()
            if drone_id in VALID_DRONE_IDS and isinstance(paths, list)
        },
    )


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Public liveness check — no auth required."""
    runtime_mode, sdn_controller = _runtime_mode_and_sdn_controller()
    return HealthResponse(
        status="ok",
        rl_loaded=len(_rl_agents) == len(VALID_DRONE_IDS),
        fl_loaded=_fl_model is not None,
        uptime_s=round(time.time() - _start_time, 2),
        mode=runtime_mode,
        sdn_controller=sdn_controller,
    )


@app.get("/ready", response_model=ReadinessResponse, tags=["System"])
async def readiness(response: Response) -> ReadinessResponse:
    """Dependency readiness check; returns 503 until the SDN data plane is usable."""
    sdn = await _fetch_sdn_readiness()
    alert = ReadinessTransition.model_validate(
        _readiness_history.record(sdn.model_dump())
    )
    if not sdn.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if sdn.ready else "not_ready",
        ready=sdn.ready,
        sdn=sdn,
        alert=alert,
    )


@app.get(
    "/ready/history",
    response_model=ReadinessHistoryResponse,
    tags=["System"],
)
async def readiness_history(
    limit: int = Query(default=20, ge=1, le=100),
    _: dict = Depends(get_current_user),
) -> ReadinessHistoryResponse:
    """Return newest-first in-process SDN readiness transitions for operators."""
    events = [
        ReadinessTransition.model_validate(event)
        for event in _readiness_history.snapshot(limit=limit)
    ]
    return ReadinessHistoryResponse(
        events=events,
        count=len(events),
        capacity=_readiness_history.max_entries,
    )


@app.post("/predict", response_model=PredictResponse, tags=["Inference"])
async def predict(
    req: PredictRequest,
    _: dict = Depends(get_current_user),
) -> PredictResponse:
    """Predict the best routing path. Requires JWT auth."""
    path_scores = req.path_scores
    fl_confidence = None
    attack_type = None

    # This endpoint receives already-computed threat scores, not the five raw RF
    # features required by the FL model. Confidence/attack fields therefore stay
    # null instead of being inferred from fabricated repeated telemetry.

    rl_agent = _rl_agents.get(req.drone_id)
    if rl_agent is not None:
        try:
            decision = rl_agent.predict(path_scores, reward=req.prev_reward)
        except Exception as e:
            logger.warning("RL predict failed: %s. Using greedy.", e)
            decision = _greedy_predict(path_scores)
    else:
        decision = _greedy_predict(path_scores)

    return PredictResponse(
        action_id=decision["action_id"],
        path_name=decision["path_name"],
        threat_level=decision["threat_level"],
        original_action_id=decision.get("original_action_id", decision["action_id"]),
        safety_override=bool(decision.get("safety_override", False)),
        safe_action_mask=decision.get("safe_action_mask", [True, True, True]),
        no_safe_route=bool(decision.get("no_safe_route", False)),
        constraint_reason=decision.get("constraint_reason"),
        safety_threshold=float(decision.get("safety_threshold", 0.8)),
        all_unsafe_behavior=decision.get(
            "all_unsafe_behavior", "least_risk_route"
        ),
        fl_confidence=fl_confidence,
        attack_type=attack_type,
        timestamp=time.time(),
    )


@app.get("/metrics/live", tags=["Metrics"])
async def live_metrics(
    drone_id: str = "drone_1",
    _: dict = Depends(get_current_user),
) -> dict:
    """Latest telemetry that actually drove a routing decision. Requires JWT auth."""
    if drone_id not in VALID_DRONE_IDS:
        raise HTTPException(status_code=400, detail=f"drone_id must be one of {sorted(VALID_DRONE_IDS)}")

    events = await _latest_decision_events()
    if drone_id in events:
        return events[drone_id]["telemetry"]

    from simulation.generator import generate_metrics
    metrics = generate_metrics(drone_id=drone_id)
    metrics["source"] = "legacy_api_fallback"
    return metrics


@app.get("/swarm/status", tags=["Swarm"])
async def swarm_status(
    _: dict = Depends(get_current_user),
) -> dict:
    """Return the last known decision for every drone in the swarm."""
    events = await _latest_decision_events()
    summaries = await _latest_run_summaries()
    result = {
        drone_id: {
            "path_name": row["path_name"],
            "threat_level": row["threat_level"],
            "reward": row["reward"],
            "step": row["step"],
            "source": "legacy_summary",
        }
        for drone_id, row in summaries.items()
    }
    result.update({
        drone_id: _decision_summary_from_event(event)
        for drone_id, event in events.items()
    })
    return {"drones": result}


@app.get("/swarm/metrics", tags=["Swarm"])
async def swarm_all_metrics(
    _: dict = Depends(get_current_user),
) -> dict:
    """Latest decision-driving RF telemetry for all drones in the swarm."""
    events = await _latest_decision_events()
    result = {
        drone_id: event["telemetry"]
        for drone_id, event in events.items()
    }
    missing = VALID_DRONE_IDS - result.keys()
    if not missing:
        return result

    from simulation.generator import generate_swarm_metrics
    generated = generate_swarm_metrics()
    for drone_id in missing:
        generated[drone_id]["source"] = "legacy_api_fallback"
        result[drone_id] = generated[drone_id]
    return result


@app.get("/metrics/history", tags=["Metrics"])
async def metrics_history(
    limit: int = Query(default=60, ge=1, le=1000),
    _: dict = Depends(get_current_user),
) -> dict:
    """Return last `limit` orchestrator decisions for charting. Requires JWT auth."""
    db_path = _BASE / "experiments" / "experiment.db"
    if not db_path.exists():
        return {"rows": []}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        columns = await _table_columns(db, "runs")
        selected_columns = _RUN_SUMMARY_COLUMNS
        if "event_json" in columns:
            selected_columns += ", event_json"
        cursor = await db.execute(
            f"SELECT {selected_columns} FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()

    # Reverse so oldest-first for charting
    result = []
    for row in reversed(rows):
        summary = dict(row)
        raw_event = summary.pop("event_json", None)
        if raw_event:
            try:
                summary["event"] = DecisionEvent.model_validate_json(raw_event).model_dump(
                    mode="json"
                )
            except Exception as exc:
                logger.warning("Ignoring invalid history decision event: %s", exc)
        result.append(summary)
    return {"rows": result, "count": len(result)}


# ---------------------------------------------------------------------------
# Jamming Control (protected)
# ---------------------------------------------------------------------------


def _clear_jamming_after(drone_id: str, duration: float, request_time: float):
    time.sleep(duration)
    # If 'all', check drone_1's timestamp as reference
    check_id = "drone_1" if drone_id == "all" else drone_id
    if _last_jam_time.get(check_id) == request_time:
        from simulation.jammer import clear_jamming
        clear_jamming(drone_id)
        logger.info("Auto-cleared jamming on %s after %.1fs", drone_id, duration)
    else:
        logger.info("Skipping auto-clear for %s: newer request detected", drone_id)


async def _wait_for_jam_visibility(
    targets: List[str],
    previous_events: Dict[str, dict],
    expected_profile: Optional[str],
    *,
    timeout_s: float = 1.25,
) -> bool:
    """Wait until an active orchestrator persists the requested jammer state."""
    now = time.time()
    if any(
        target not in previous_events
        or now - float(previous_events[target]["timestamp"]) > 2.0
        for target in targets
    ):
        return False

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current_events = await _latest_decision_events()
        visible = True
        for target in targets:
            current = current_events.get(target)
            previous = previous_events.get(target)
            if current is None or current.get("event_id") == previous.get("event_id"):
                visible = False
                break
            active_attack = (
                current.get("telemetry", {})
                .get("ew_status", {})
                .get("active_attack")
            )
            if active_attack != expected_profile:
                visible = False
                break
        if visible:
            return True
        await asyncio.sleep(0.05)
    return False


@app.post("/jam", tags=["Control"])
async def trigger_jamming(
    req: JamRequest,
    bg_tasks: BackgroundTasks,
    _: dict = Depends(require_admin),
):
    """Trigger or clear jamming. Requires JWT auth."""
    from simulation.generator import DRONES
    from simulation.jammer import clear_jamming, set_jamming_state

    targets = DRONES if req.drone_id == "all" else [req.drone_id]
    previous_events = await _latest_decision_events()
    if req.profile == "none":
        clear_jamming(req.drone_id)
        now = time.time()
        for t in targets:
            _last_jam_time[t] = now
        await _wait_for_jam_visibility(
            targets,
            previous_events,
            None,
        )
        return {"status": "cleared", "drone_id": req.drone_id}

    targets = set_jamming_state(req.drone_id, req.paths, req.profile)
    
    req_time = time.time()
    for t in targets:
        _last_jam_time[t] = req_time
        
    bg_tasks.add_task(_clear_jamming_after, req.drone_id, req.duration, req_time)
    await _wait_for_jam_visibility(
        targets,
        previous_events,
        req.profile,
    )
    return {
        "status": "jamming",
        "drone_id": req.drone_id,
        "paths": req.paths,
        "duration": req.duration,
        "profile": req.profile,
    }


# ---------------------------------------------------------------------------
# Byzantine & Swarm Security Control
# ---------------------------------------------------------------------------

@app.post("/swarm/compromise/{drone_id}", tags=["Control"])
async def compromise_drone(drone_id: str, _: dict = Depends(require_admin)):
    """Mark a drone as compromised (triggers simulated Byzantine model poisoning)."""
    from simulation.generator import DRONES
    if drone_id not in DRONES:
        raise HTTPException(status_code=400, detail=f"Invalid drone_id: {drone_id}")
    _compromised_drones.add(drone_id)
    logger.info("Byzantine fault active: %s marked as COMPROMISED (model poisoning).", drone_id)
    return {"status": "compromised", "drone_id": drone_id}


@app.post("/swarm/restore/{drone_id}", tags=["Control"])
async def restore_drone(drone_id: str, _: dict = Depends(require_admin)):
    """Restore a previously compromised drone to healthy status."""
    if drone_id not in VALID_DRONE_IDS:
        raise HTTPException(status_code=400, detail=f"Invalid drone_id: {drone_id}")
    if drone_id in _compromised_drones:
        _compromised_drones.remove(drone_id)
    logger.info("Byzantine fault cleared: %s RESTORED to normal operations.", drone_id)
    return {"status": "normal", "drone_id": drone_id}
 
 
# ---------------------------------------------------------------------------
# Federated Learning Controls & Metrics API
# ---------------------------------------------------------------------------

@app.get("/api/fl/config", tags=["Federated Learning"])
async def get_fl_config(_: dict = Depends(get_current_user)):
    """Retrieve the current Federated Learning config from config/fl_config.yaml."""
    cfg_path = _BASE / "config" / "fl_config.yaml"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="FL config file not found")
    try:
        cfg = yaml.safe_load(cfg_path.read_text())
        return cfg
    except Exception as e:
        logger.exception("Failed to read FL config")
        raise HTTPException(status_code=500, detail="Failed to read FL config") from e

def _validate_config_patch(patch: Any, current: Any, path: str = "config") -> None:
    """Reject unknown keys, shape changes, non-finite numbers, and type changes."""
    if not isinstance(patch, dict) or not patch:
        raise HTTPException(status_code=422, detail="Configuration patch must be a non-empty object")
    if not isinstance(current, dict):
        raise HTTPException(status_code=422, detail=f"{path} is not an object")

    for key, value in patch.items():
        child_path = f"{path}.{key}"
        if key not in current:
            raise HTTPException(status_code=422, detail=f"Unknown configuration key: {child_path}")
        expected = current[key]
        if isinstance(expected, dict):
            if not isinstance(value, dict):
                raise HTTPException(status_code=422, detail=f"{child_path} must be an object")
            _validate_config_patch(value, expected, child_path)
        elif isinstance(expected, bool):
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail=f"{child_path} must be a boolean")
        elif isinstance(expected, int) and not isinstance(expected, bool):
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(status_code=422, detail=f"{child_path} must be an integer")
            if not math.isfinite(float(value)):
                raise HTTPException(status_code=422, detail=f"{child_path} must be finite")
        elif isinstance(expected, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=422, detail=f"{child_path} must be numeric")
            if not math.isfinite(float(value)):
                raise HTTPException(status_code=422, detail=f"{child_path} must be finite")
        elif not isinstance(value, type(expected)):
            raise HTTPException(
                status_code=422,
                detail=f"{child_path} must have type {type(expected).__name__}",
            )


def _deep_merge(current: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict):
            _deep_merge(current[key], value)
        elif isinstance(current[key], float) and isinstance(value, (int, float)):
            # JSON has no integer-vs-float distinction for values such as 1.0.
            # Preserve the schema implied by the existing YAML configuration.
            current[key] = float(value)
        else:
            current[key] = value
    return current


def _write_yaml_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            yaml.safe_dump(value, temp_file, default_flow_style=False, sort_keys=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


@app.post("/api/fl/config", tags=["Federated Learning"])
async def update_fl_config(new_config: dict, _: dict = Depends(require_admin)):
    """Update the Federated Learning config in config/fl_config.yaml."""
    cfg_path = _BASE / "config" / "fl_config.yaml"
    try:
        if not cfg_path.exists():
            raise HTTPException(status_code=404, detail="FL config file not found")
        current_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        _validate_config_patch(new_config, current_cfg)
        updated_cfg = _deep_merge(current_cfg, new_config)
        _write_yaml_atomic(cfg_path, updated_cfg)
        logger.info("FL config updated dynamically via API.")
        return {"status": "success", "config": updated_cfg}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update FL config")
        raise HTTPException(status_code=500, detail="Failed to update FL config") from e

@app.get("/api/fl/metrics", tags=["Federated Learning"])
async def get_fl_metrics(_: dict = Depends(get_current_user)):
    """Retrieve the latest Federated Learning metrics from results/fl_metrics_snapshot.json."""
    metrics_path = _BASE / "results" / "fl_metrics_snapshot.json"
    if not metrics_path.exists():
        return {
            "last_round": 0,
            "convergence_round": None,
            "n_rounds_completed": 0,
            "latest": {
                "round": 0,
                "threat_f1": 0.0,
                "attack_accuracy": 0.0,
                "confidence_brier": None,
                "round_loss": 0.0,
                "privacy_epsilon": 0.0,
                "privacy_delta": 1e-5,
                "compression_ratio": 1.0,
                "mean_trust": 1.0,
                "min_trust": 1.0,
                "quarantine_rate": 0.0,
                "jains_fairness": 1.0,
                "drift_rate": 0.0,
                "mean_drift_score": 0.0,
                "kd_loss": 0.0
            },
            "history": []
        }
    try:
        with open(metrics_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Failed to read FL metrics")
        raise HTTPException(status_code=500, detail="Failed to read FL metrics") from e


@app.get("/api/report/generate", tags=["Report"])
async def generate_evaluation_report(
    _: dict = Depends(require_admin),
):
    """Generate and export a premium Capstone Evaluation Report in HTML format."""
    from fastapi.responses import HTMLResponse
    db_path = _BASE / "experiments" / "experiment.db"
    
    # 1. Gather stats from SQLite (or defaults if missing)
    total_steps = 0
    path_dist = {"direct": 0, "satellite": 0, "mesh": 0}
    avg_reward = 0.0
    avg_loss = 0.0
    
    if db_path.exists():
        try:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                # Total steps
                cursor = await db.execute("SELECT COUNT(*) as count FROM runs")
                row = await cursor.fetchone()
                total_steps = row["count"] if row else 0
                
                # Path distribution
                cursor = await db.execute("SELECT path_name, COUNT(*) as count FROM runs GROUP BY path_name")
                rows = await cursor.fetchall()
                total_paths = 0
                for r in rows:
                    pname = r["path_name"]
                    pcount = r["count"]
                    if pname in path_dist:
                        path_dist[pname] = pcount
                        total_paths += pcount
                if total_paths > 0:
                    for k in path_dist:
                        path_dist[k] = round((path_dist[k] / total_paths) * 100, 1)
                        
                # Averages
                cursor = await db.execute("SELECT AVG(reward) as reward, AVG(packet_loss) as loss FROM runs")
                row = await cursor.fetchone()
                if row:
                    avg_reward = round(row["reward"] or 0.0, 4)
                    avg_loss = round((row["loss"] or 0.0) * 100, 2)
        except Exception as e:
            logger.error("Report database read failed: %s", e)
            
    has_sufficient_data = total_steps >= 10
    report_notice = (
        "Metrics below were calculated from recorded experiment data."
        if has_sufficient_data
        else "Insufficient experiment data: fewer than 10 recorded decisions are available. "
             "No demonstration values have been substituted."
    )

    # 2. Build premium HTML template
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Capstone Evaluation Report — Team 2</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap');
            :root {{
                --bg: #0b0f19;
                --card-bg: #111827;
                --border: #1f2937;
                --text: #f3f4f6;
                --text-muted: #9ca3af;
                --accent-blue: #3b82f6;
                --accent-emerald: #10b981;
                --accent-rose: #f43f5e;
            }}
            * {{
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }}
            body {{
                font-family: 'Inter', sans-serif;
                background-color: var(--bg);
                color: var(--text);
                padding: 40px 20px;
                line-height: 1.6;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background-color: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            }}
            header {{
                border-bottom: 2px solid var(--border);
                padding-bottom: 30px;
                margin-bottom: 30px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            h1 {{
                font-size: 28px;
                font-weight: 800;
                color: #fff;
                letter-spacing: -0.5px;
            }}
            .subtitle {{
                font-size: 14px;
                color: var(--accent-blue);
                text-transform: uppercase;
                letter-spacing: 2px;
                font-weight: 700;
                margin-bottom: 5px;
            }}
            .metadata {{
                font-size: 13px;
                color: var(--text-muted);
                text-align: right;
            }}
            .btn-print {{
                background-color: var(--accent-blue);
                color: #fff;
                border: none;
                padding: 10px 20px;
                border-radius: 10px;
                font-weight: 600;
                cursor: pointer;
                transition: background-color 0.2s;
                font-size: 13px;
            }}
            .btn-print:hover {{
                background-color: #2563eb;
            }}
            .grid {{
                display: grid;
                grid-template-cols: repeat(4, 1fr);
                gap: 20px;
                margin-bottom: 35px;
            }}
            .stat-box {{
                background-color: rgba(255,255,255,0.02);
                border: 1px solid var(--border);
                border-radius: 15px;
                padding: 20px;
                text-align: center;
            }}
            .stat-val {{
                font-size: 24px;
                font-weight: 800;
                color: #fff;
                font-family: 'JetBrains Mono', monospace;
                margin-top: 5px;
            }}
            .stat-label {{
                font-size: 11px;
                color: var(--text-muted);
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            .notice {{
                margin-bottom: 24px;
                padding: 12px 16px;
                border-radius: 10px;
                color: var(--text);
                background: rgba(59,130,246,0.08);
                border: 1px solid rgba(59,130,246,0.25);
            }}
            h2 {{
                font-size: 20px;
                font-weight: 700;
                margin: 30px 0 15px 0;
                color: #fff;
                border-left: 4px solid var(--accent-blue);
                padding-left: 12px;
            }}
            p {{
                color: var(--text-muted);
                font-size: 15px;
                margin-bottom: 20px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 30px;
                font-size: 14px;
            }}
            th {{
                background-color: rgba(255,255,255,0.02);
                color: #fff;
                text-align: left;
                padding: 12px;
                border-bottom: 2px solid var(--border);
                text-transform: uppercase;
                font-size: 11px;
                letter-spacing: 1px;
            }}
            td {{
                padding: 12px;
                border-bottom: 1px solid var(--border);
                color: var(--text);
            }}
            .badge {{
                display: inline-block;
                padding: 3px 8px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .badge-success {{ background-color: rgba(16,185,129,0.1); color: var(--accent-emerald); border: 1px solid rgba(16,185,129,0.2); }}
            .badge-warn {{ background-color: rgba(244,63,94,0.1); color: var(--accent-rose); border: 1px solid rgba(244,63,94,0.2); }}
            footer {{
                margin-top: 50px;
                border-top: 1px solid var(--border);
                padding-top: 20px;
                text-align: center;
                font-size: 12px;
                color: var(--text-muted);
            }}
            @media print {{
                body {{ background: #fff; color: #000; padding: 0; }}
                .container {{ box-shadow: none; border: none; max-width: 100%; padding: 0; }}
                .btn-print {{ display: none; }}
                .stat-box {{ border: 1px solid #ddd; }}
                .stat-val, h1, h2 {{ color: #000; }}
                td, th {{ border-bottom: 1px solid #ddd; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div>
                    <div class="subtitle">Tactical Performance Report</div>
                    <h1>FLARE COMMAND CENTER EVALUATION</h1>
                </div>
                <div class="metadata">
                    <button class="btn-print" onclick="window.print()">Print Evaluation Report</button>
                    <div style="margin-top: 10px;">Swarm Status: <span class="badge badge-success">Active</span></div>
                </div>
            </header>

            <div class="notice">{report_notice}</div>

            <div class="grid">
                <div class="stat-box">
                    <div class="stat-label">Total Steps Run</div>
                    <div class="stat-val">{total_steps}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Avg RL Reward</div>
                    <div class="stat-val">{avg_reward}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Mean Packet Loss</div>
                    <div class="stat-val">{avg_loss}%</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Direct Path Usage</div>
                    <div class="stat-val">{path_dist["direct"]}%</div>
                </div>
            </div>

            <h2>1. System Performance Overview</h2>
            <p>This report summarizes the experiment decisions currently stored by FLARE. Path distribution and averages are descriptive values from the available records; they are not model-accuracy or convergence claims.</p>

            <h2>2. Communication Link Interface Path Distribution</h2>
            <table>
                <thead>
                    <tr>
                        <th>Link Name</th>
                        <th>OVS Port</th>
                        <th>Path Share</th>
                        <th>Expected Latency</th>
                        <th>Design Quality</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Direct Path (s2)</td>
                        <td>Port 1</td>
                        <td>{path_dist["direct"]}%</td>
                        <td>5 - 30 ms</td>
                        <td><span class="badge badge-success">Optimal Link</span></td>
                    </tr>
                    <tr>
                        <td>Mesh Path (s4)</td>
                        <td>Port 3</td>
                        <td>{path_dist["mesh"]}%</td>
                        <td>50 - 200 ms</td>
                        <td><span class="badge badge-success">Secondary Link</span></td>
                    </tr>
                    <tr>
                        <td>Satellite Path (s3)</td>
                        <td>Port 2</td>
                        <td>{path_dist["satellite"]}%</td>
                        <td>250 - 800 ms</td>
                        <td><span class="badge badge-warn">Fallback Link</span></td>
                    </tr>
                </tbody>
            </table>

            <h2>3. Secure Aggregation Audit Summary</h2>
            <p>The configured aggregation pipeline can apply update clipping, anomaly filtering, trust-weighted or robust averaging, and server-side noise. The exact method used in a round must be verified from the exported FL round metrics rather than inferred from this report.</p>

            <footer>
                Capstone Project Team 2 — Grade Evaluation Deliverable. Compiled dynamically at {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}.
            </footer>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ---------------------------------------------------------------------------
# Explainable AI (XAI) Local Perturbation Engine
# ---------------------------------------------------------------------------

def _calculate_xai_attributions(
    model,
    path_metrics: dict | None,
    path_history: Optional[List[dict]] = None,
) -> dict:
    """
    Calculate local feature attribution weights for the 5 RF metrics
    using input perturbation study on the current path metrics.
    """
    import numpy as np
    import torch
    
    # Defaults in case model is not loaded or metrics are missing
    defaults = {"rssi": 15, "pdr": 30, "sinr": 40, "latency": 10, "packet_loss": 5}
    if model is None or not path_metrics:
        return defaults
        
    try:
        # Normalize the oldest-first rolling path history. At startup, left-pad
        # the earliest real observation until a full model sequence is available.
        mins  = np.array([-120.0, 0.0, -10.0,  0.0,   0.0], dtype=np.float32)
        maxs  = np.array([ -20.0, 1.0,  30.0, 1000.0, 1.0], dtype=np.float32)
        history = list(path_history or [path_metrics])[-_FL_SEQUENCE_LEN:]
        frames = []
        for snapshot in history:
            features = np.array([
                snapshot.get("rssi", -50.0),
                snapshot.get("pdr", 0.9),
                snapshot.get("sinr", 20.0),
                snapshot.get("latency", 20.0),
                snapshot.get("packet_loss", 0.0),
            ], dtype=np.float32)
            frames.append(np.clip((features - mins) / (maxs - mins + 1e-8), 0.0, 1.0))
        if len(frames) < _FL_SEQUENCE_LEN:
            frames = [frames[0]] * (_FL_SEQUENCE_LEN - len(frames)) + frames
        seq = np.stack(frames).astype(np.float32)
        
        # Build one base sequence and five feature-ablation variants, then run
        # all six in a single model call.
        variants = [seq]
        for j in range(5):
            seq_pert = seq.copy()
            seq_pert[:, j] = 0.0  # Ablate feature
            variants.append(seq_pert)

        x_batch = torch.tensor(np.stack(variants), dtype=torch.float32)
        with torch.no_grad():
            output = model(x_batch)
            predictions = output.path_scores.mean(dim=1).cpu().numpy()

        p_base = float(predictions[0])
        influences = [
            abs(p_base - float(p_pert)) + 0.02
            for p_pert in predictions[1:]
        ]
            
        total = sum(influences)
        if total > 0:
            pcts = [int((inf / total) * 100) for inf in influences]
            # Ensure they sum strictly to 100
            diff = 100 - sum(pcts)
            pcts[0] += diff
        else:
            pcts = [20, 20, 20, 20, 20]
            
        return {
            "rssi": pcts[0],
            "pdr": pcts[1],
            "sinr": pcts[2],
            "latency": pcts[3],
            "packet_loss": pcts[4],
        }
    except Exception as e:
        logger.error("XAI perturbation failed: %s. Using default weights.", e)
        return defaults


# ---------------------------------------------------------------------------
# Real-time Streams
# ---------------------------------------------------------------------------


async def _broadcast_to_ws(data: dict):
    """Push a message to all connected WebSocket clients."""
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


async def telemetry_broadcaster():
    """Broadcast the persisted telemetry/decision pair produced by the orchestrator."""

    while True:
        try:
            if _ws_clients:
                events = await _latest_decision_events()
                legacy_summaries = await _latest_run_summaries()
                missing = VALID_DRONE_IDS - events.keys()
                fallback_metrics: Dict[str, dict] = {}
                if missing:
                    from simulation.generator import generate_swarm_metrics
                    fallback_metrics = generate_swarm_metrics()
                    for drone_id in missing:
                        fallback_metrics[drone_id]["source"] = "legacy_api_fallback"

                from simulation.generator import _load_jam_state
                jam_state = _load_jam_state()

                import numpy as np
                byzantine_status = []
                for drone in sorted(VALID_DRONE_IDS):
                    drone_jam = jam_state.get(drone, {"profile": "none"})
                    profile = drone_jam.get("profile", "none")
                    is_comp = drone in _compromised_drones or profile in ["model_poisoning", "data_poisoning"]

                    anomaly_score = 4.2 + np.random.uniform(0.5, 1.5) if is_comp else 0.2 + np.random.uniform(0.1, 0.3)
                    byzantine_status.append({
                        "drone_id": drone,
                        "status": "COMPROMISED" if is_comp else "NORMAL",
                        "anomaly_score": round(anomaly_score, 2),
                        "z_score": round(anomaly_score * 0.8, 2),
                        "action": "REJECTED" if is_comp else "ACCEPTED"
                    })

                fl_metrics = None
                fl_metrics_path = _BASE / "results" / "fl_metrics_snapshot.json"
                if fl_metrics_path.exists():
                    try:
                        with open(fl_metrics_path, "r") as f:
                            fl_metrics = json.load(f)
                    except Exception:
                        pass

                for drone_id in sorted(VALID_DRONE_IDS):
                    event = events.get(drone_id)
                    if event is not None:
                        decision = _decision_summary_from_event(event)
                        metrics = event["telemetry"]
                        message_timestamp = event["timestamp"]
                    else:
                        legacy = legacy_summaries.get(drone_id, {})
                        decision = {
                            "path_name": legacy.get("path_name", "direct"),
                            "threat_level": legacy.get("threat_level", "UNKNOWN"),
                            "reward": legacy.get("reward", 0.0),
                            "step": legacy.get("step", 0),
                            "source": "legacy_summary" if legacy else "api_fallback",
                            "fallback_reasons": ["canonical_event_unavailable"],
                        }
                        metrics = fallback_metrics[drone_id]
                        message_timestamp = metrics["timestamp"]
                    active_path = decision.get("path_name", "direct")
                    paths = metrics.get("paths", [])
                    history_token = (
                        event["event_id"]
                        if event is not None
                        else f"fallback:{message_timestamp}"
                    )
                    if _xai_last_event_ids.get(drone_id) != history_token:
                        for path in paths:
                            path_name = path.get("path_id")
                            if path_name in _xai_metric_history[drone_id]:
                                _xai_metric_history[drone_id][path_name].append(path)
                        _xai_last_event_ids[drone_id] = history_token
                    active_path_metrics = next(
                        (
                            path
                            for path in paths
                            if path.get("path_id") == active_path
                        ),
                        paths[0] if paths else None,
                    )
                    await _broadcast_to_ws({
                        "type": "telemetry",
                        "timestamp": message_timestamp,
                        "decision": decision,
                        "metrics": metrics,
                        "event": event,
                        "telemetry_age_s": max(0.0, time.time() - message_timestamp),
                        "xai": _calculate_xai_attributions(
                            _fl_model,
                            active_path_metrics,
                            list(_xai_metric_history[drone_id][active_path]),
                        ),
                        "byzantine": byzantine_status,
                        "fl_metrics": fl_metrics,
                    })
        except Exception as e:
            logger.error("Telemetry broadcaster error: %s", e)

        await asyncio.sleep(1.0)


@app.get("/stream", tags=["Stream"])
async def stream_decisions(_: dict = Depends(get_current_user)):
    """
    SSE stream of live orchestrator decisions.
    Requires a bearer token. Streaming clients must support Authorization headers.
    """
    async def event_generator():
        db_path = _BASE / "experiments" / "experiment.db"
        last_id: Optional[int] = None
        while True:
            if db_path.exists():
                try:
                    async with aiosqlite.connect(db_path) as db:
                        db.row_factory = aiosqlite.Row
                        columns = await _table_columns(db, "runs")
                        selected_columns = _RUN_SUMMARY_COLUMNS
                        if "event_json" in columns:
                            selected_columns += ", event_json"
                        if last_id is None:
                            cursor = await db.execute(
                                f"SELECT {selected_columns} FROM runs "
                                "ORDER BY id DESC LIMIT 3"
                            )
                            rows = list(reversed(await cursor.fetchall()))
                        else:
                            cursor = await db.execute(
                                f"SELECT {selected_columns} FROM runs "
                                "WHERE id > ? ORDER BY id ASC LIMIT 100",
                                (last_id,),
                            )
                            rows = await cursor.fetchall()
                        for row in rows:
                            last_id = row["id"]
                            payload = dict(row)
                            raw_event = payload.pop("event_json", None)
                            if raw_event:
                                payload = DecisionEvent.model_validate_json(raw_event).model_dump(
                                    mode="json"
                                )
                            yield {"event": "decision", "data": json.dumps(payload)}
                except Exception as e:
                    logger.debug("SSE DB read error: %s", e)
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time orchestrator decisions.
    The first client message must be JSON: {"type": "auth", "token": "..."}.
    """
    await websocket.accept()
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        if not isinstance(auth_message, dict):
            raise ValueError("Invalid WebSocket auth message")
        if auth_message.get("type") != "auth" or not isinstance(auth_message.get("token"), str):
            raise ValueError("Missing WebSocket auth message")
        _authenticate_token(auth_message["token"])
    except (asyncio.TimeoutError, ValueError, HTTPException, json.JSONDecodeError):
        await websocket.close(code=4401, reason="Authentication required")
        return

    await websocket.send_json({"type": "auth", "status": "ok"})
    _ws_clients.append(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_ws_clients))
    try:
        while True:
            # Keep-alive: wait for any client message (ping)
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
        logger.info("WebSocket client disconnected. Total: %d", len(_ws_clients))


# ---------------------------------------------------------------------------
# Static Files — mounted LAST so API routes take priority
# ---------------------------------------------------------------------------

configured_static_dir = os.getenv("AJ_STATIC_DIR")
frontend_dir = (
    Path(configured_static_dir)
    if configured_static_dir
    else _BASE / "frontend-react" / "dist"
)
if (frontend_dir / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    async def api_root() -> dict:
        return {
            "service": "FLARE API",
            "docs": "/api/docs",
            "health": "/health",
            "readiness": "/ready",
        }


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=False)
