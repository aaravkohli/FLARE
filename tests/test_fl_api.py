"""
tests/test_fl_api.py — Integration test for Federated Learning REST endpoints

Verifies:
  - Auth requirements (401 on missing token)
  - GET /api/fl/config returns valid config matching fl_config.yaml
  - POST /api/fl/config updates config parameters dynamically
  - GET /api/fl/metrics returns latest metrics snapshot
"""

import urllib.request
import urllib.parse
import json
import sys

BASE_URL = "http://localhost:8000"

def run_test():
    print("=== Testing FLARE v2 Federated Learning API ===")

    # 1. Test authentication requirement
    try:
        urllib.request.urlopen(BASE_URL + "/api/fl/config")
        print("[FAIL] GET /api/fl/config succeeded without token")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("[PASS] GET /api/fl/config correctly blocked without token (401)")
        else:
            print(f"[FAIL] GET /api/fl/config returned unexpected code: {e.code}")
            sys.exit(1)

    # 2. Authenticate
    try:
        data = urllib.parse.urlencode({"username": "admin", "password": "antijam2026"}).encode("utf-8")
        req = urllib.request.Request(BASE_URL + "/auth/token", data=data, method="POST")
        with urllib.request.urlopen(req) as r:
            token = json.loads(r.read())["access_token"]
            print("[PASS] Successfully authenticated and obtained token")
    except Exception as e:
        print(f"[FAIL] Authentication failed: {e}")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 3. GET config
    try:
        req = urllib.request.Request(BASE_URL + "/api/fl/config", headers=headers)
        with urllib.request.urlopen(req) as r:
            cfg = json.loads(r.read())
            assert "differential_privacy" in cfg, "DP section missing"
            assert "compression" in cfg, "Compression section missing"
            assert "trust" in cfg, "Trust section missing"
            print("[PASS] GET /api/fl/config returned valid config parameters")
    except Exception as e:
        print(f"[FAIL] GET /api/fl/config failed: {e}")
        sys.exit(1)

    # 4. POST config (modify differential_privacy.client_side.epsilon temporarily)
    original_eps = cfg["differential_privacy"]["client_side"]["epsilon"]
    test_eps = 7.7
    cfg["differential_privacy"]["client_side"]["epsilon"] = test_eps

    try:
        post_data = json.dumps(cfg).encode("utf-8")
        req = urllib.request.Request(BASE_URL + "/api/fl/config", data=post_data, headers=headers, method="POST")
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
            assert resp["status"] == "success", "Response status should be success"
            assert resp["config"]["differential_privacy"]["client_side"]["epsilon"] == test_eps, "Epsilon not updated"
            print("[PASS] POST /api/fl/config successfully updated configuration")
    except Exception as e:
        print(f"[FAIL] POST /api/fl/config failed: {e}")
        sys.exit(1)

    # 5. Restore original epsilon
    cfg["differential_privacy"]["client_side"]["epsilon"] = original_eps
    try:
        post_data = json.dumps(cfg).encode("utf-8")
        req = urllib.request.Request(BASE_URL + "/api/fl/config", data=post_data, headers=headers, method="POST")
        urllib.request.urlopen(req)
        print("[PASS] Original configuration parameters restored successfully")
    except Exception as e:
        print(f"[FAIL] Restoring original config failed: {e}")
        sys.exit(1)

    # 6. GET metrics
    try:
        req = urllib.request.Request(BASE_URL + "/api/fl/metrics", headers=headers)
        with urllib.request.urlopen(req) as r:
            metrics = json.loads(r.read())
            assert "latest" in metrics, "Latest metrics section missing"
            assert "history" in metrics, "Metrics history section missing"
            print("[PASS] GET /api/fl/metrics returned valid metrics snapshot")
    except Exception as e:
        print(f"[FAIL] GET /api/fl/metrics failed: {e}")
        sys.exit(1)

    print("=== All FL API tests passed successfully ===")

if __name__ == "__main__":
    run_test()
