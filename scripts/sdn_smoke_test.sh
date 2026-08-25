#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_SUFFIX="${GITHUB_RUN_ID:-local}-$$"
NETWORK_NAME="flare-sdn-smoke-${RUN_SUFFIX}"
CONTROLLER_NAME="flare-sdn-controller-${RUN_SUFFIX}"
MININET_NAME="flare-mininet-${RUN_SUFFIX}"
LOG_VOLUME="flare-sdn-logs-${RUN_SUFFIX}"
SDN_IMAGE="${FLARE_SDN_IMAGE:-flare-sdn-smoke:latest}"
MININET_IMAGE="${FLARE_MININET_IMAGE:-flare-mininet-smoke:latest}"
SDN_TOKEN="${AJ_SDN_TOKEN:-flare-sdn-smoke-token-at-least-24-characters}"
controller_mounts=()
mininet_mounts=()

if [[ "${FLARE_BIND_SOURCE:-0}" == "1" ]]; then
  controller_mounts=(
    -v "$PROJECT_ROOT/sdn:/app/sdn:ro"
    -v "$PROJECT_ROOT/config:/app/config:ro"
  )
  mininet_mounts=(
    -v "$PROJECT_ROOT/sdn:/app/sdn:ro"
    -v "$PROJECT_ROOT/config:/app/config:ro"
  )
fi

cleanup() {
  exit_code=$?
  trap - EXIT
  if [[ $exit_code -ne 0 ]]; then
    docker logs "$CONTROLLER_NAME" 2>/dev/null || true
    docker logs "$MININET_NAME" 2>/dev/null || true
  fi
  docker rm -f "$MININET_NAME" "$CONTROLLER_NAME" >/dev/null 2>&1 || true
  docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
  docker volume rm "$LOG_VOLUME" >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap cleanup EXIT

wait_for_command() {
  description="$1"
  shift
  for _attempt in $(seq 1 60); do
    if "$@" >/dev/null 2>&1; then
      echo "PASS: ${description}"
      return 0
    fi
    sleep 1
  done
  echo "FAIL: timed out waiting for ${description}" >&2
  return 1
}

verify_route() {
  payload="$1"
  expected_path="$2"
  python3 -c '
import json
import sys

payload = json.loads(sys.argv[1])
expected_path = sys.argv[2]
assert payload["status"] == "ok", payload
assert payload["installed_path"] == expected_path, payload
assert payload["acknowledged_switches"] == [1, 2, 3, 4, 5], payload
' "$payload" "$expected_path"
}

if [[ "${FLARE_SKIP_DOCKER_BUILD:-0}" != "1" ]]; then
  docker build -f "$PROJECT_ROOT/docker/Dockerfile.sdn" -t "$SDN_IMAGE" "$PROJECT_ROOT"
  docker build -f "$PROJECT_ROOT/docker/Dockerfile.mininet" -t "$MININET_IMAGE" "$PROJECT_ROOT"
fi

docker network create "$NETWORK_NAME" >/dev/null
docker volume create "$LOG_VOLUME" >/dev/null

docker run -d \
  --name "$CONTROLLER_NAME" \
  --network "$NETWORK_NAME" \
  --network-alias sdn-controller \
  -e AJ_ENV=production \
  -e AJ_SDN_TOKEN="$SDN_TOKEN" \
  -v "$LOG_VOLUME:/app/logs" \
  "${controller_mounts[@]}" \
  "$SDN_IMAGE" \
  ryu-manager sdn/controller.py --ofp-tcp-listen-port 6633 >/dev/null

wait_for_command "Ryu liveness" \
  docker exec "$CONTROLLER_NAME" curl -fsS http://127.0.0.1:8080/health

docker run -d \
  --name "$MININET_NAME" \
  --privileged \
  --network "$NETWORK_NAME" \
  -v "$LOG_VOLUME:/app/logs" \
  "${mininet_mounts[@]}" \
  "$MININET_IMAGE" >/dev/null

wait_for_command "five-switch OpenFlow topology readiness" \
  docker exec "$CONTROLLER_NAME" curl -fsS http://127.0.0.1:8080/ready

direct_result="$(
  docker exec "$CONTROLLER_NAME" curl -fsS \
    -H "Authorization: Bearer ${SDN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"path_name":"direct","action_id":0,"drone_id":"drone_1"}' \
    http://127.0.0.1:8080/sdn/route
)"
verify_route "$direct_result" "direct"
echo "PASS: direct route acknowledged by every expected switch"

docker exec "$MININET_NAME" ovs-ofctl mod-port s1 s1-eth1 down

failover_result=""
for _attempt in $(seq 1 20); do
  failover_result="$(
    docker exec "$CONTROLLER_NAME" curl -fsS \
      -H "Authorization: Bearer ${SDN_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"path_name":"direct","action_id":0,"drone_id":"drone_1"}' \
      http://127.0.0.1:8080/sdn/route
  )"
  if verify_route "$failover_result" "satellite" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
verify_route "$failover_result" "satellite"
echo "PASS: direct-link failure produced acknowledged satellite failover"

docker exec "$MININET_NAME" ovs-ofctl mod-port s1 s1-eth1 up
echo "SDN integration smoke test passed."
