"""
sdn/mock_sdn.py — [MOCK] [SIMULATION MODE ONLY]
Mock SDN REST stub that simulates flow rule installation without
requiring Open vSwitch or a real Ryu controller.

Runs as a lightweight FastAPI server on port 8080.
Accepts the same POST /sdn/route request as the real Ryu controller.
Logs all decisions to logs/mock_sdn.log.

Usage:
  python sdn/mock_sdn.py
"""

import logging
import hmac
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path when running as `python sdn/mock_sdn.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sdn.route_contract import (
    ROUTABLE_PATHS,
    VALID_DRONES,
    VALID_PATHS,
    action_id_for_path,
    normalize_installed_path,
    validate_route_action,
)

_BASE = Path(__file__).parent.parent
_SDN_CFG = yaml.safe_load((_BASE / "config" / "sdn_config.yaml").read_text())
_MOCK_CFG = _SDN_CFG.get("mock", {})
_FAILOVER = _SDN_CFG["failover_priority"]

_DEVELOPMENT_SDN_TOKEN = "antijam-development-sdn-token"
SDN_API_TOKEN = os.getenv("AJ_SDN_TOKEN", _DEVELOPMENT_SDN_TOKEN)
if os.getenv("AJ_ENV", "development").lower() in {"production", "prod"} and (
    SDN_API_TOKEN == _DEVELOPMENT_SDN_TOKEN or len(SDN_API_TOKEN) < 24
):
    raise RuntimeError(
        "AJ_SDN_TOKEN must be set to a unique value of at least 24 characters in production"
    )

# Setup logging
Path(_BASE / "logs").mkdir(exist_ok=True)
log_path = _BASE / _MOCK_CFG.get("log_path", "logs/mock_sdn.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MOCK-SDN] %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Mock SDN Controller", version="1.0.0")

# Simulated switch state
_flow_table: dict = {}
_available_paths = {"direct", "satellite", "mesh"}  # All paths up by default


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_name: str
    action_id: int = Field(ge=0, le=2)
    drone_id: str = "drone_1"
    priority: int = Field(default=100, ge=1, le=65535)

    @field_validator("path_name")
    @classmethod
    def validate_path(cls, v):
        if v not in ROUTABLE_PATHS:
            raise ValueError(f"Invalid path: {v}. Must be one of {ROUTABLE_PATHS}")
        return v

    @field_validator("drone_id")
    @classmethod
    def validate_drone(cls, value):
        if value not in VALID_DRONES:
            raise ValueError(f"Invalid drone_id: {value}. Must be one of {VALID_DRONES}")
        return value

    @model_validator(mode="after")
    def validate_route_contract(self):
        validate_route_action(self.path_name, self.action_id)
        return self


class RouteResponse(BaseModel):
    status: str
    installed_path: str
    installed_action_id: int
    flow_priority: int
    timestamp: float
    note: str


def require_sdn_token(authorization: str | None = Header(default=None)) -> None:
    scheme, _, supplied_token = (authorization or "").partition(" ")
    authenticated = (
        scheme.lower() == "bearer"
        and bool(supplied_token)
        and hmac.compare_digest(supplied_token, SDN_API_TOKEN)
    )
    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SDN service token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _select_safe_path(requested: str) -> str:
    """Walk failover priority until an available path is found."""
    if requested in _available_paths:
        return requested
    for fallback in _FAILOVER:
        if fallback in _available_paths or fallback == "fallback":
            logger.warning(
                "Requested path '%s' unavailable. Falling back to '%s'.", requested, fallback
            )
            return fallback
    return "fallback"


@app.post("/sdn/route", response_model=RouteResponse)
async def install_route(
    req: RouteRequest,
    _: None = Depends(require_sdn_token),
) -> RouteResponse:
    """
    Simulate installing a flow rule on the virtual switch.
    Implements deterministic failover if the requested path is unavailable.
    """
    safe_path = _select_safe_path(req.path_name)
    installed_path = normalize_installed_path(safe_path)
    installed_action_id = action_id_for_path(installed_path)
    priority = max(
        _SDN_CFG["flow_priority"][path]
        for path in ("direct", "satellite", "mesh")
    )

    # Update simulated flow table
    _flow_table[req.drone_id] = {
        "path": installed_path,
        "action_id": installed_action_id,
        "priority": priority,
        "installed_at": time.time(),
    }

    note = "DIRECT INSTALL" if safe_path == req.path_name else f"FAILOVER from {req.path_name}"
    logger.info(
        "drone=%s | path=%s | priority=%d | %s",
        req.drone_id, safe_path, priority, note,
    )

    return RouteResponse(
        status="ok",
        installed_path=installed_path,
        installed_action_id=installed_action_id,
        flow_priority=priority,
        timestamp=time.time(),
        note=note,
    )


@app.get("/sdn/flows")
async def get_flow_table(_: None = Depends(require_sdn_token)) -> dict:
    """Return current simulated flow table."""
    return {"flow_table": _flow_table, "available_paths": list(_available_paths)}


@app.post("/sdn/simulate/fail/{path}")
async def simulate_path_failure(
    path: str,
    _: None = Depends(require_sdn_token),
) -> dict:
    """Mark a path as unavailable (for testing failover)."""
    if path not in VALID_PATHS:
        raise HTTPException(status_code=400, detail=f"Unknown path: {path}")
    _available_paths.discard(path)
    logger.warning("Path '%s' marked as UNAVAILABLE (simulated failure).", path)
    return {"status": "ok", "unavailable_paths": list(VALID_PATHS - _available_paths)}


@app.post("/sdn/simulate/restore/{path}")
async def simulate_path_restore(
    path: str,
    _: None = Depends(require_sdn_token),
) -> dict:
    """Restore a previously failed path."""
    _available_paths.add(path)
    logger.info("Path '%s' RESTORED.", path)
    return {"status": "ok", "available_paths": list(_available_paths)}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mode": "mock", "flows": len(_flow_table)}


@app.get("/ready")
async def readiness() -> dict:
    """Expose the same readiness shape as the real controller."""
    available_paths = [
        path for path in ROUTABLE_PATHS if path in _available_paths
    ]
    return {
        "status": "ready",
        "mode": "mock",
        "topology": {
            "ready": True,
            "expected_switches": [],
            "connected_switches": [],
            "inventory_complete_switches": [],
            "ports": {},
            "available_paths": {
                drone_id: available_paths for drone_id in sorted(VALID_DRONES)
            },
        },
    }


if __name__ == "__main__":
    port = _SDN_CFG["controller"]["port"]
    logger.info("Mock SDN controller starting on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
