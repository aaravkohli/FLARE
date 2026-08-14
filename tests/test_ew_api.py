"""
tests/test_ew_api.py — Integration test for upgraded SOTA EW endpoints

Verifies:
  - Triggering smart jamming via POST /jam
  - Triggering GPS spoofing via POST /jam
  - Confirming generator parses EW state and returns correct indicators
"""

import urllib.request
import urllib.parse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

BASE_URL = "http://localhost:8000"

def run_test():
    print("=== Testing SOTA Electronic Warfare API ===")

    # 1. Authenticate
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

    # 2. Trigger smart jamming
    try:
        payload = {
            "drone_id": "drone_1",
            "paths": ["direct"],
            "duration": 5.0,
            "profile": "smart"
        }
        req = urllib.request.Request(BASE_URL + "/jam", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
            assert resp["status"] == "jamming"
            assert resp["profile"] == "smart"
            print("[PASS] POST /jam successfully triggered Smart Jamming")
    except Exception as e:
        print(f"[FAIL] Smart Jamming trigger failed: {e}")
        sys.exit(1)

    # 3. Check generator output under smart jamming
    # Since we triggered it on drone_1, generate_metrics should reflect it
    try:
        from simulation.generator import generate_metrics
        m = generate_metrics(drone_id="drone_1")
        
        # Verify direct path has smart metrics (low PDR, normal RSSI)
        direct_metrics = None
        for p in m["paths"]:
            if p["path_id"] == "direct":
                direct_metrics = p
                break
        
        assert direct_metrics is not None
        assert direct_metrics["pdr"] < 0.10, f"Smart PDR should be low, got {direct_metrics['pdr']}"
        assert direct_metrics["rssi"] > -90, f"Smart RSSI should be relatively normal, got {direct_metrics['rssi']}"
        print("[PASS] Generator simulated Smart Jamming metrics correctly")
    except Exception as e:
        print(f"[FAIL] Generator Smart Jamming validation failed: {e}")
        sys.exit(1)

    # 4. Trigger GPS Spoofing
    try:
        payload = {
            "drone_id": "drone_2",
            "paths": [],
            "duration": 5.0,
            "profile": "gps_spoofing"
        }
        req = urllib.request.Request(BASE_URL + "/jam", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
            assert resp["status"] == "jamming"
            assert resp["profile"] == "gps_spoofing"
            print("[PASS] POST /jam successfully triggered GPS Spoofing")
    except Exception as e:
        print(f"[FAIL] GPS Spoofing trigger failed: {e}")
        sys.exit(1)

    # 5. Check GPS coordinates drift in generator
    try:
        m = generate_metrics(drone_id="drone_2")
        assert m["gps"]["drift_m"] == 120.0, f"Expected 120.0m GPS drift, got {m['gps']['drift_m']}"
        print("[PASS] Generator simulated GPS coordinates drift correctly")
    except Exception as e:
        print(f"[FAIL] GPS Spoofing validation failed: {e}")
        sys.exit(1)

    # Clear jamming
    try:
        payload = {
            "drone_id": "drone_1",
            "paths": [],
            "duration": 0.0,
            "profile": "none"
        }
        req = urllib.request.Request(BASE_URL + "/jam", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        urllib.request.urlopen(req)
        print("[PASS] Jamming cleared successfully")
    except Exception as e:
        print(f"[FAIL] Clearing jamming failed: {e}")
        sys.exit(1)

    print("=== All SOTA EW integration tests passed successfully ===")

if __name__ == "__main__":
    run_test()
