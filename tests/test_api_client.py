"""
tests/test_api_client.py — In-Memory FastAPI TestClient Suite for FLARE API
"""

import sys
from pathlib import Path

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))

import pytest
from fastapi.testclient import TestClient
from api.server import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_s" in data
    print("[PASS] GET /health endpoint verified")


def _login_token() -> str:
    # Login with default admin credentials
    response = client.post("/auth/token", data={"username": "admin", "password": "antijam2026"})
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data
    return token_data["access_token"]


def test_auth_login():
    _login_token()
    print("[PASS] POST /auth/token verified")


@pytest.fixture
def token():
    return _login_token()


def test_predict_and_metrics(token):
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test /predict
    payload = {"path_scores": [0.85, 0.15, 0.10], "drone_id": "drone_1"}
    resp = client.post("/predict", json=payload, headers=headers)
    assert resp.status_code == 200
    pred = resp.json()
    assert pred["threat_level"] == "HIGH"
    assert pred["path_name"] in ["direct", "satellite", "mesh"]
    print("[PASS] POST /predict endpoint verified")

    # 2. Test /metrics/live
    resp = client.get("/metrics/live?drone_id=drone_1", headers=headers)
    assert resp.status_code == 200
    metrics = resp.json()
    assert "paths" in metrics
    print("[PASS] GET /metrics/live endpoint verified")

    # 3. Test /api/fl/config
    resp = client.get("/api/fl/config", headers=headers)
    assert resp.status_code == 200
    cfg = resp.json()
    assert "differential_privacy" in cfg
    print("[PASS] GET /api/fl/config endpoint verified")


def run_all_tests():
    print("=== Running FLARE API In-Memory Unit Test Suite ===")
    test_health()
    test_auth_login()
    test_predict_and_metrics(_login_token())
    print("=== All API In-Memory Unit Tests Passed (100% Success) ===")


if __name__ == "__main__":
    run_all_tests()
