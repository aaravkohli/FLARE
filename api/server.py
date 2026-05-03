"""
api/server.py — Anti-Jamming Drone System API

Endpoints:
  POST /auth/token       — login, returns JWT token
  POST /predict          — accepts path_scores, returns best path (protected)
  GET  /health           — liveness check (public)
  GET  /metrics/live     — latest raw RF metrics snapshot (protected)
  GET  /metrics/history  — last N decisions from SQLite for charting (protected)
  POST /jam              — trigger jamming simulation (protected)
  GET  /stream           — SSE stream of orchestrator decisions (public for UI)
  WS   /ws               — WebSocket stream of decisions (for future clients)
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
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
)
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

_BASE = Path(__file__).parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [API] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth Configuration
# ---------------------------------------------------------------------------

# IMPORTANT: In production, set this via environment variable and rotate it!
SECRET_KEY = os.getenv("AJ_SECRET_KEY", "antijam-super-secret-key-change-in-prod-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8-hour sessions

pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# Simple in-memory user store — in production, use a DB
# Passwords are sha256_crypt hashed. Default: admin/antijam2026
_USERS: dict = {}  # filled at module load below


def _build_users():
    """Build hashed user store at startup to avoid bcrypt 72-byte bug."""
    return {
        "admin": {
            "username": "admin",
            "hashed_password": pwd_context.hash("antijam2026"),
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


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
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


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class Token(BaseModel):
    access_token: str
    token_type: str
    username: str


class PredictRequest(BaseModel):
    path_scores: List[float]
    drone_id: str = "drone_1"
    prev_reward: float = 0.0

    @field_validator("path_scores")
    @classmethod
    def validate_scores(cls, v):
        if len(v) != 3:
            raise ValueError("path_scores must have exactly 3 values (direct, satellite, mesh)")
        for s in v:
            if not (0.0 <= s <= 1.0):
                raise ValueError(f"All scores must be in [0, 1], got {s}")
        return v


class PredictResponse(BaseModel):
    action_id: int
    path_name: str
    threat_level: str
    fl_confidence: Optional[float] = None
    attack_type: Optional[str] = None
    timestamp: float


class HealthResponse(BaseModel):
    status: str
    rl_loaded: bool
    fl_loaded: bool
    uptime_s: float
    version: str = "2.0.0"


class JamRequest(BaseModel):
    paths: List[str]
    duration: float = 10.0
    drone_id: str = "drone_1"


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

_rl_agent = None
_fl_model = None
_start_time = time.time()
_last_jam_time: Dict[str, float] = {}  # drone_id -> timestamp of last jam request
_ws_clients: List[WebSocket] = []  # Connected WebSocket clients


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _rl_agent, _fl_model

    rl_model_path = _BASE / "models" / "rl_model.zip"
    if rl_model_path.exists():
        try:
            from rl.agent import RLAgent
            _rl_agent = RLAgent(str(rl_model_path))
            logger.info("RL agent loaded.")
        except Exception as e:
            logger.warning("Failed to load RL agent: %s. Using greedy fallback.", e)
    else:
        logger.warning("rl_model.zip not found. Run `python rl/train.py` first.")

    fl_model_path = _BASE / "models" / "fl_model.pth"
    if fl_model_path.exists():
        try:
            from fl.model import build_model
            fl_cfg = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
            _fl_model = build_model(fl_cfg["model"])
            _fl_model.load_state_dict(torch.load(fl_model_path, map_location="cpu"))
            _fl_model.eval()
            logger.info("FL model loaded.")
        except Exception as e:
            logger.warning("Failed to load FL model: %s", e)

    yield
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


# ---------------------------------------------------------------------------
# Auth Endpoints
# ---------------------------------------------------------------------------


@app.post("/auth/token", response_model=Token, tags=["Auth"])
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Authenticate and retrieve a JWT bearer token."""
    user = _USERS.get(form_data.username)
    if not user or not _verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_access_token(
        data={"sub": user["username"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=token, token_type="bearer", username=user["username"])


# ---------------------------------------------------------------------------
# Core Endpoints
# ---------------------------------------------------------------------------


def _greedy_predict(path_scores: List[float]) -> dict:
    action = int(min(range(3), key=lambda i: path_scores[i]))
    paths = ["direct", "satellite", "mesh"]
    max_score = max(path_scores)
    level = "HIGH" if max_score >= 0.7 else ("MEDIUM" if max_score >= 0.4 else "LOW")
    return {"action_id": action, "path_name": paths[action], "threat_level": level}


VALID_DRONE_IDS = {"drone_1", "drone_2", "drone_3"}


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Public liveness check — no auth required."""
    return HealthResponse(
        status="ok",
        rl_loaded=_rl_agent is not None,
        fl_loaded=_fl_model is not None,
        uptime_s=round(time.time() - _start_time, 2),
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

    if _fl_model is not None:
        try:
            import numpy as np
            seq = np.tile(
                np.array([[s, s, 1 - s, s * 100, s * 0.5]
                           for s in path_scores[:1]], dtype=np.float32),
                (10, 1),
            )
            x = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
            with torch.no_grad():
                out = _fl_model(x)
            fl_confidence = float(out.confidence.squeeze().item())
            attack_idx = int(out.attack_logits.argmax(dim=-1).item())
            from fl.model import ATTACK_CLASSES
            attack_type = ATTACK_CLASSES[attack_idx]
        except Exception as e:
            logger.debug("FL inference failed: %s", e)

    if _rl_agent is not None:
        try:
            decision = _rl_agent.predict(path_scores, reward=req.prev_reward)
        except Exception as e:
            logger.warning("RL predict failed: %s. Using greedy.", e)
            decision = _greedy_predict(path_scores)
    else:
        decision = _greedy_predict(path_scores)

    return PredictResponse(
        action_id=decision["action_id"],
        path_name=decision["path_name"],
        threat_level=decision["threat_level"],
        fl_confidence=fl_confidence,
        attack_type=attack_type,
        timestamp=time.time(),
    )


@app.get("/metrics/live", tags=["Metrics"])
async def live_metrics(
    drone_id: str = "drone_1",
    _: dict = Depends(get_current_user),
) -> dict:
    """Live RF metrics snapshot. Requires JWT auth."""
    if drone_id not in VALID_DRONE_IDS:
        raise HTTPException(status_code=400, detail=f"drone_id must be one of {sorted(VALID_DRONE_IDS)}")
    from simulation.generator import generate_metrics
    return generate_metrics(drone_id=drone_id)


@app.get("/swarm/status", tags=["Swarm"])
async def swarm_status(
    _: dict = Depends(get_current_user),
) -> dict:
    """Return the last known decision for every drone in the swarm."""
    db_path = _BASE / "experiments" / "experiment.db"
    if not db_path.exists():
        return {"drones": {}}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Get the latest row for each drone
        cursor = await db.execute(
            "SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY drone_id)"
        )
        rows = await cursor.fetchall()

    result = {}
    for row in rows:
        result[row["drone_id"]] = {
            "path_name": row["path_name"],
            "threat_level": row["threat_level"],
            "reward": row["reward"],
            "step": row["step"],
            "source": "orchestrator",
        }
    return {"drones": result}


@app.get("/swarm/metrics", tags=["Swarm"])
async def swarm_all_metrics(
    _: dict = Depends(get_current_user),
) -> dict:
    """Full RF telemetry for all drones in the swarm."""
    from simulation.generator import generate_swarm_metrics
    return generate_swarm_metrics()


@app.get("/metrics/history", tags=["Metrics"])
async def metrics_history(
    limit: int = 60,
    _: dict = Depends(get_current_user),
) -> dict:
    """Return last `limit` orchestrator decisions for charting. Requires JWT auth."""
    db_path = _BASE / "experiments" / "experiment.db"
    if not db_path.exists():
        return {"rows": []}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()

    # Reverse so oldest-first for charting
    result = [dict(r) for r in reversed(rows)]
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


@app.post("/jam", tags=["Control"])
async def trigger_jamming(
    req: JamRequest,
    bg_tasks: BackgroundTasks,
    _: dict = Depends(get_current_user),
):
    """Trigger or clear jamming. Requires JWT auth."""
    from simulation.jammer import jam_paths, clear_jamming, VALID_PATHS

    if not all(p in VALID_PATHS for p in req.paths):
        raise HTTPException(
            status_code=400,
            detail=f"Paths must be subset of {VALID_PATHS}",
        )

    if not req.paths:
        clear_jamming(req.drone_id)
        # Update last_jam_time to cancel any pending auto-clears
        now = time.time()
        targets = DRONES if req.drone_id == "all" else [req.drone_id]
        for t in targets:
            _last_jam_time[t] = now
        return {"status": "cleared", "drone_id": req.drone_id}

    # Set state directly instead of using the blocking jam_paths function
    from simulation.jammer import _load_state, _write_state
    from simulation.generator import DRONES
    state = _load_state()

    targets = DRONES if req.drone_id == "all" else [req.drone_id]
    
    for target_id in targets:
        if target_id not in state:
            state[target_id] = {p: False for p in VALID_PATHS}
        for p in VALID_PATHS:
            state[target_id][p] = (p in req.paths)
            
    _write_state(state)
    
    req_time = time.time()
    for t in targets:
        _last_jam_time[t] = req_time
        
    bg_tasks.add_task(_clear_jamming_after, req.drone_id, req.duration, req_time)
    return {"status": "jamming", "drone_id": req.drone_id, "paths": req.paths, "duration": req.duration}


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
        _ws_clients.remove(ws)


@app.get("/stream", tags=["Stream"])
async def stream_decisions():
    """
    SSE stream of live orchestrator decisions.
    Public (no auth) so the browser dashboard can connect without CORS complications.
    """
    async def event_generator():
        db_path = _BASE / "experiments" / "experiment.db"
        last_id = -1
        while True:
            if db_path.exists():
                try:
                    async with aiosqlite.connect(db_path) as db:
                        db.row_factory = aiosqlite.Row
                        cursor = await db.execute(
                            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
                        )
                        row = await cursor.fetchone()
                        if row and row["id"] > last_id:
                            last_id = row["id"]
                            payload = dict(row)
                            yield {"event": "decision", "data": json.dumps(payload)}
                            # Also broadcast to WS clients
                            await _broadcast_to_ws(payload)
                except Exception as e:
                    logger.debug("SSE DB read error: %s", e)
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time orchestrator decisions.
    Designed for future native clients (mobile apps, Unity-based simulators, etc.)
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_ws_clients))
    try:
        while True:
            # Keep-alive: wait for any client message (ping)
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.remove(websocket)
        logger.info("WebSocket client disconnected. Total: %d", len(_ws_clients))


# ---------------------------------------------------------------------------
# Static Files — mounted LAST so API routes take priority
# ---------------------------------------------------------------------------

frontend_dir = _BASE / "frontend"
frontend_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=False)
