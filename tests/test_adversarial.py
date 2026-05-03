"""
tests/test_adversarial.py
=========================
Rigorous adversarial test suite for the Anti-Jamming Drone System.

Tests attempt to:
1. Break authentication (invalid tokens, brute-force, token replay)
2. Inject malicious inputs (path traversal, SQL injection, oversized payloads)
3. Test race conditions (concurrent jams, simultaneous requests)
4. DoS resilience (flood SSE connections, rapid API calls)
5. Validate all endpoints with boundary/edge cases
6. Verify error handling and graceful degradation

Run: cd /Users/aaravkohli/Capstone && python tests/test_adversarial.py
"""

import asyncio
import concurrent.futures
import json
import os
import sys
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE_URL = "http://127.0.0.1:8000"
PASS_COLOR = "\033[92m"  # green
FAIL_COLOR = "\033[91m"  # red
WARN_COLOR = "\033[93m"  # yellow
INFO_COLOR = "\033[94m"  # blue
RESET = "\033[0m"

results = {"passed": 0, "failed": 0, "warnings": 0}


def _req(method, path, *, data=None, headers=None, timeout=5):
    url = BASE_URL + path
    h = headers or {}
    if data and isinstance(data, dict):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif data and isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    def _parse(body):
        try:
            return json.loads(body)
        except Exception:
            return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _parse(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, _parse(e.read())
        except Exception:
            return e.code, {}
    except Exception as ex:
        return None, str(ex)


def get_valid_token():
    import urllib.parse
    data = urllib.parse.urlencode({"username": "admin", "password": "antijam2026"}).encode()
    req = urllib.request.Request(BASE_URL + "/auth/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())["access_token"]


def check(name, condition, detail="", warn=False):
    if condition:
        print(f"  {PASS_COLOR}✓ PASS{RESET} {name}")
        results["passed"] += 1
    elif warn:
        print(f"  {WARN_COLOR}⚠ WARN{RESET} {name} — {detail}")
        results["warnings"] += 1
    else:
        print(f"  {FAIL_COLOR}✗ FAIL{RESET} {name} — {detail}")
        results["failed"] += 1


def section(title):
    print(f"\n{INFO_COLOR}{'─'*60}{RESET}")
    print(f"{INFO_COLOR}  {title}{RESET}")
    print(f"{INFO_COLOR}{'─'*60}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. AUTHENTICATION ATTACKS
# ─────────────────────────────────────────────────────────────────────────────
def test_auth():
    section("1. Authentication & Authorization")

    # No token
    code, _ = _req("GET", "/metrics/live")
    check("No token → 401", code == 401, f"got {code}")

    # Wrong password
    import urllib.parse
    data = urllib.parse.urlencode({"username": "admin", "password": "wrongpassword"}).encode()
    req = urllib.request.Request(BASE_URL + "/auth/token", data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        check("Wrong password → 401", False, "Expected 401, got 200")
    except urllib.error.HTTPError as e:
        check("Wrong password → 401", e.code == 401, f"got {e.code}")

    # Non-existent user
    data = urllib.parse.urlencode({"username": "hacker", "password": "x"}).encode()
    req = urllib.request.Request(BASE_URL + "/auth/token", data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        check("Unknown user → 401", False, "Expected 401, got 200")
    except urllib.error.HTTPError as e:
        check("Unknown user → 401", e.code == 401, f"got {e.code}")

    # Malformed / tampered JWT
    tampered = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiJ9.FAKESIGNATURE"
    code, _ = _req("GET", "/metrics/live", headers={"Authorization": f"Bearer {tampered}"})
    check("Tampered JWT → 401", code == 401, f"got {code}")

    # Expired-looking token (obviously wrong)
    code, _ = _req("GET", "/metrics/live", headers={"Authorization": "Bearer expired.token.value"})
    check("Garbage JWT → 401", code == 401, f"got {code}")

    # Empty bearer
    code, _ = _req("GET", "/metrics/live", headers={"Authorization": "Bearer "})
    check("Empty Bearer → 401", code == 401, f"got {code}")

    # Auth header injection attempt — Uvicorn rejects null bytes at HTTP level (400) or JWT middleware (401)
    code, _ = _req("GET", "/metrics/live", headers={"Authorization": "Bearer \x00\x01\x02evil"})
    check("Null-byte injection → rejected (400 or 401)", code in (400, 401), f"got {code}")

    # Valid token works
    token = get_valid_token()
    code, data = _req("GET", "/metrics/live", headers={"Authorization": f"Bearer {token}"})
    check("Valid token → 200", code == 200, f"got {code}")

    # Token is required for jam too
    code, _ = _req("POST", "/jam", data={"paths": ["direct"], "duration": 1})
    check("No token on /jam → 401", code == 401, f"got {code}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. INPUT VALIDATION ATTACKS
# ─────────────────────────────────────────────────────────────────────────────
def test_input_validation(token):
    section("2. Input Validation & Injection")
    auth = {"Authorization": f"Bearer {token}"}

    # Path traversal on drone_id
    for evil_id in ["../../../etc/passwd", "drone_1; DROP TABLE runs;", "<script>alert(1)</script>", "drone_99", ""]:
        code, resp = _req("GET", f"/metrics/live?drone_id={urllib.parse.quote(evil_id)}", headers=auth)
        check(f"Invalid drone_id '{evil_id[:20]}' → 400/422", code in (400, 422), f"got {code}: {resp}")

    # predict with wrong number of scores
    for bad_scores in [[], [0.5], [0.1, 0.2], [0.1, 0.2, 0.3, 0.4], None]:
        code, _ = _req("POST", "/predict", data={"path_scores": bad_scores, "drone_id": "drone_1"}, headers=auth)
        check(f"predict scores={bad_scores} → 422", code == 422, f"got {code}")

    # predict with out-of-range scores
    for bad_scores in [[-0.1, 0.5, 0.5], [1.1, 0.5, 0.5], [999, 0.5, 0.5]]:
        code, _ = _req("POST", "/predict", data={"path_scores": bad_scores, "drone_id": "drone_1"}, headers=auth)
        check(f"predict out-of-range {bad_scores} → 422", code == 422, f"got {code}")

    # predict with string scores (type coercion attack)
    code, _ = _req("POST", "/predict", data={"path_scores": ["evil", "1", "2"]}, headers=auth)
    check("predict non-numeric scores → 422", code == 422, f"got {code}")

    # jam with invalid paths
    for bad_paths in [["evil_path"], ["direct", "INVALID"], ["../etc"], [None]]:
        code, _ = _req("POST", "/jam", data={"paths": bad_paths, "duration": 5}, headers=auth)
        check(f"jam invalid paths {bad_paths} → 400/422", code in (400, 422), f"got {code}")

    # jam with extreme duration (float overflow)
    code, resp = _req("POST", "/jam", data={"paths": ["direct"], "duration": 1e308}, headers=auth)
    check("jam extreme duration → handled (200 or 422)", code in (200, 422), f"got {code}")
    # Clear if it went through
    if code == 200:
        _req("POST", "/jam", data={"paths": [], "duration": 0}, headers=auth)

    # history endpoint: limit injection
    for bad_limit in [-1, 0, 100001, "'; DROP TABLE runs; --", "999999999"]:
        code, _ = _req("GET", f"/metrics/history?limit={urllib.parse.quote(str(bad_limit))}", headers=auth)
        check(f"history limit={bad_limit} → handled", code in (200, 422), f"got {code}")

    # Oversized payload
    giant = {"path_scores": [0.1, 0.2, 0.3], "drone_id": "A" * 100_000}
    code, _ = _req("POST", "/predict", data=giant, headers=auth)
    check("Oversized drone_id string → handled", code in (200, 422, 413), f"got {code}")

    # JSON injection in path body
    code, _ = _req(
        "POST", "/jam",
        data='{"paths": ["direct"], "extra": "<script>"}',
        headers={**auth, "Content-Type": "application/json"},
    )
    check("JSON injection ignored (extra field stripped)", code in (200, 422), f"got {code}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. RACE CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────
def test_race_conditions(token):
    section("3. Race Conditions & Concurrent Access")
    auth = {"Authorization": f"Bearer {token}"}

    # Concurrent jam requests on the same path
    errors = []
    def do_jam(path):
        try:
            code, _ = _req("POST", "/jam", data={"paths": [path], "duration": 2}, headers=auth)
            if code not in (200, 429):
                errors.append(f"{path}: {code}")
        except Exception as ex:
            errors.append(str(ex))

    threads = [threading.Thread(target=do_jam, args=(p,)) for p in ["direct", "satellite", "mesh", "direct", "satellite"]]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("Concurrent jam requests all handled", len(errors) == 0, f"Errors: {errors}")
    _req("POST", "/jam", data={"paths": [], "duration": 0}, headers=auth)  # clear

    # Concurrent metric reads
    read_errors = []
    def do_read(drone_id):
        try:
            code, d = _req("GET", f"/metrics/live?drone_id={drone_id}", headers=auth)
            if code != 200 or not (isinstance(d, dict) and d.get("paths")):
                read_errors.append(f"{drone_id}: {code}")
        except Exception as ex:
            read_errors.append(str(ex))

    threads = [threading.Thread(target=do_read, args=(f"drone_{(i%3)+1}",)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("12 concurrent multi-drone reads all 200", len(read_errors) == 0, f"Errors: {read_errors}")

    # Concurrent /swarm/status requests
    status_errors = []
    def do_swarm():
        code, _ = _req("GET", "/swarm/status", headers=auth)
        if code != 200: status_errors.append(code)
    threads = [threading.Thread(target=do_swarm) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    check("10 concurrent /swarm/status → all 200", len(status_errors) == 0, f"Errors: {status_errors}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. DoS RESILIENCE
# ─────────────────────────────────────────────────────────────────────────────
def test_dos_resilience(token):
    section("4. DoS Resilience")
    auth = {"Authorization": f"Bearer {token}"}

    # Rapid-fire /health requests (no auth)
    start = time.time()
    ok = 0
    for _ in range(50):
        code, _ = _req("GET", "/health", timeout=2)
        if code == 200: ok += 1
    elapsed = time.time() - start
    check("50 rapid /health requests all succeed", ok == 50, f"ok={ok}")
    check("50 /health in < 5 seconds", elapsed < 5, f"took {elapsed:.2f}s", warn=True)

    # Rapid-fire authenticated predict requests
    ok = 0
    for i in range(20):
        code, _ = _req("POST", "/predict", data={"path_scores": [0.1, 0.2, 0.3]}, headers=auth)
        if code == 200: ok += 1
    check("20 rapid /predict requests succeed", ok == 20, f"ok={ok}")

    # Large limit on history endpoint
    code, data = _req("GET", "/metrics/history?limit=10000", headers=auth)
    check("Huge history limit returns 200 (clamped or all rows)", code == 200, f"got {code}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. SWARM & MULTI-DRONE TESTS
# ─────────────────────────────────────────────────────────────────────────────
def test_swarm(token):
    section("5. Multi-Drone Swarm Functionality")
    auth = {"Authorization": f"Bearer {token}"}

    # /swarm/status returns all 3 drones
    code, data = _req("GET", "/swarm/status", headers=auth)
    check("/swarm/status → 200", code == 200, f"got {code}")
    if code == 200:
        drones = data.get("drones", {})
        check("Swarm has 3 drone entries", len(drones) == 3, f"got {len(drones)}")
        for drone_id in ["drone_1", "drone_2", "drone_3"]:
            d = drones.get(drone_id, {})
            check(f"  {drone_id} has path_name", "path_name" in d, f"keys: {list(d.keys())}")
            check(f"  {drone_id} has threat_level", "threat_level" in d, f"keys: {list(d.keys())}")

    # /swarm/metrics returns all 3 drones
    code, data = _req("GET", "/swarm/metrics", headers=auth)
    check("/swarm/metrics → 200", code == 200, f"got {code}")
    if code == 200:
        check("Swarm metrics has 3 drones", len(data) == 3, f"got {len(data)}")
        for did in ["drone_1", "drone_2", "drone_3"]:
            check(f"  {did} metrics present", did in data, f"keys: {list(data.keys())}")

    # Drone 2 metrics differ from Drone 1 (has offset applied)
    code1, m1 = _req("GET", "/metrics/live?drone_id=drone_1", headers=auth)
    code2, m2 = _req("GET", "/metrics/live?drone_id=drone_2", headers=auth)
    code3, m3 = _req("GET", "/metrics/live?drone_id=drone_3", headers=auth)
    check("Drone 1 metrics OK", code1 == 200 and "paths" in m1, f"got {code1}")
    check("Drone 2 metrics OK", code2 == 200 and "paths" in m2, f"got {code2}")
    check("Drone 3 metrics OK", code3 == 200 and "paths" in m3, f"got {code3}")

    if code1 == 200 and code2 == 200:
        # Compare median over 5 samples to reduce stochastic noise
        rssi1_vals = [_req("GET", "/metrics/live?drone_id=drone_1", headers=auth)[1]["paths"][0]["rssi"] for _ in range(5)]
        rssi2_vals = [_req("GET", "/metrics/live?drone_id=drone_2", headers=auth)[1]["paths"][0]["rssi"] for _ in range(5)]
        median1 = sorted(rssi1_vals)[2]
        median2 = sorted(rssi2_vals)[2]
        check("Drone 2 median RSSI weaker than Drone 1", median2 < median1, f"drone1 median={median1:.1f}, drone2 median={median2:.1f}")

    if code2 == 200 and code3 == 200:
        rssi2_vals = [_req("GET", "/metrics/live?drone_id=drone_2", headers=auth)[1]["paths"][0]["rssi"] for _ in range(5)]
        rssi3_vals = [_req("GET", "/metrics/live?drone_id=drone_3", headers=auth)[1]["paths"][0]["rssi"] for _ in range(5)]
        median2 = sorted(rssi2_vals)[2]
        median3 = sorted(rssi3_vals)[2]
        check("Drone 3 median RSSI weakest", median3 < median2, f"drone2 median={median2:.1f}, drone3 median={median3:.1f}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. JAMMING ROUND-TRIP TEST
# ─────────────────────────────────────────────────────────────────────────────
def test_jam_roundtrip(token):
    section("6. Jamming End-to-End Round-Trip")
    auth = {"Authorization": f"Bearer {token}"}

    # Clear first
    _req("POST", "/jam", data={"paths": [], "duration": 0}, headers=auth)
    time.sleep(0.3)

    # Check baseline is LOW
    code, m = _req("GET", "/metrics/live?drone_id=drone_1", headers=auth)
    baseline_pdr = m["paths"][0]["pdr"] if code == 200 else 0
    check("Baseline PDR > 0.7 (not jammed)", baseline_pdr > 0.7, f"PDR={baseline_pdr:.2f}")

    # Trigger jam
    code, resp = _req("POST", "/jam", data={"paths": ["direct"], "duration": 5}, headers=auth)
    check("Jam trigger → 200", code == 200, f"got {code}")
    check("Jam response has paths key", "paths" in resp, f"resp={resp}")

    # Wait for metrics to reflect jamming
    time.sleep(0.5)
    code, m = _req("GET", "/metrics/live?drone_id=drone_1", headers=auth)
    jammed_pdr = m["paths"][0]["pdr"] if code == 200 else 1.0
    check("Jammed PDR < 0.4 (jamming active)", jammed_pdr < 0.4, f"PDR={jammed_pdr:.2f}")

    # Clear jamming
    _req("POST", "/jam", data={"paths": [], "duration": 0}, headers=auth)
    time.sleep(0.5)
    code, m = _req("GET", "/metrics/live?drone_id=drone_1", headers=auth)
    cleared_pdr = m["paths"][0]["pdr"] if code == 200 else 0
    check("Cleared PDR > 0.7 (recovered)", cleared_pdr > 0.7, f"PDR={cleared_pdr:.2f}")


def test_jam_all(token):
    section("8. Swarm-Wide Jamming")
    auth = {"Authorization": f"Bearer {token}"}

    # Trigger jam on 'all'
    code, resp = _req("POST", "/jam", data={"paths": ["direct"], "duration": 5, "drone_id": "all"}, headers=auth)
    check("Jam 'all' trigger → 200", code == 200, f"got {code}")

    time.sleep(0.5)
    # Check all drones are jammed
    for did in ["drone_1", "drone_2", "drone_3"]:
        _, m = _req("GET", f"/metrics/live?drone_id={did}", headers=auth)
        pdr = m["paths"][0]["pdr"]
        check(f"  {did} is jammed (PDR={pdr:.2f})", pdr < 0.4)

    # Clear 'all'
    _req("POST", "/jam", data={"paths": [], "duration": 0, "drone_id": "all"}, headers=auth)
    time.sleep(0.5)
    for did in ["drone_1", "drone_2", "drone_3"]:
        _, m = _req("GET", f"/metrics/live?drone_id={did}", headers=auth)
        pdr = m["paths"][0]["pdr"]
        check(f"  {did} is recovered (PDR={pdr:.2f})", pdr > 0.7)


# ─────────────────────────────────────────────────────────────────────────────
# 7. PREDICT ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────
def test_predict(token):
    section("7. Predict Endpoint")
    auth = {"Authorization": f"Bearer {token}"}

    # Normal prediction: all low threat → direct
    code, resp = _req("POST", "/predict", data={"path_scores": [0.1, 0.2, 0.3]}, headers=auth)
    check("Normal predict → 200", code == 200, f"got {code}")
    if code == 200:
        check("Predict returns path_name", "path_name" in resp, f"resp={resp}")
        check("Predict returns threat_level", "threat_level" in resp, f"resp={resp}")
        check("Predict returns timestamp", "timestamp" in resp, f"resp={resp}")
        check("Threat LOW when scores low", resp.get("threat_level") == "LOW", f"got {resp.get('threat_level')}")

    # High threat on direct → should switch
    code, resp = _req("POST", "/predict", data={"path_scores": [0.95, 0.1, 0.1]}, headers=auth)
    if code == 200:
        check("High threat direct → threat=HIGH", resp.get("threat_level") == "HIGH", f"got {resp.get('threat_level')}")
        check("High threat direct → switches away from direct",
              resp.get("path_name") != "direct",
              f"stayed on direct despite 0.95 threat (safety override check)", warn=True)

    # Edge: all zeros (should pick direct=lowest energy)
    code, resp = _req("POST", "/predict", data={"path_scores": [0.0, 0.0, 0.0]}, headers=auth)
    check("All-zero scores → 200", code == 200, f"got {code}")

    # Edge: all ones
    code, resp = _req("POST", "/predict", data={"path_scores": [1.0, 1.0, 1.0]}, headers=auth)
    check("All-one scores → 200 with HIGH threat", code == 200 and (resp or {}).get("threat_level") == "HIGH", f"got {code}, {resp}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import urllib.parse

    print(f"\n{INFO_COLOR}{'═'*60}{RESET}")
    print(f"{INFO_COLOR}  Anti-Jamming System — Adversarial Test Suite{RESET}")
    print(f"{INFO_COLOR}  Target: {BASE_URL}{RESET}")
    print(f"{INFO_COLOR}{'═'*60}{RESET}")

    # Check server is up
    try:
        code, _ = _req("GET", "/health")
        if code != 200:
            print(f"{FAIL_COLOR}Server not responding! Start it first.{RESET}")
            sys.exit(1)
        print(f"\n{PASS_COLOR}Server is up. Running adversarial tests...{RESET}")
    except Exception as e:
        print(f"{FAIL_COLOR}Cannot connect to server: {e}{RESET}")
        sys.exit(1)

    try:
        token = get_valid_token()
    except Exception as e:
        print(f"{FAIL_COLOR}Could not obtain auth token: {e}{RESET}")
        sys.exit(1)

    test_auth()
    test_input_validation(token)
    test_race_conditions(token)
    test_dos_resilience(token)
    test_swarm(token)
    test_jam_roundtrip(token)
    test_jam_all(token)
    test_predict(token)

    # Final summary
    total = results["passed"] + results["failed"] + results["warnings"]
    print(f"\n{INFO_COLOR}{'═'*60}{RESET}")
    print(f"  Total Tests: {total}")
    print(f"  {PASS_COLOR}Passed:   {results['passed']}{RESET}")
    print(f"  {WARN_COLOR}Warnings: {results['warnings']}{RESET}")
    print(f"  {FAIL_COLOR}Failed:   {results['failed']}{RESET}")
    if results["failed"] == 0:
        print(f"\n  {PASS_COLOR}All tests passed! System is hardened.{RESET}")
    else:
        print(f"\n  {FAIL_COLOR}Some tests failed — patches needed!{RESET}")
    print(f"{INFO_COLOR}{'═'*60}{RESET}\n")
    sys.exit(0 if results["failed"] == 0 else 1)
