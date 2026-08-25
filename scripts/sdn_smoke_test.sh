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
RESULT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/flare-sdn-smoke.XXXXXX")"
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
  rm -f \
    "$RESULT_DIR/drone_1.json" \
    "$RESULT_DIR/drone_2.json" \
    "$RESULT_DIR/drone_3.json"
  rmdir "$RESULT_DIR" 2>/dev/null || true
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

request_route() {
  path_name="$1"
  action_id="$2"
  drone_id="$3"
  docker exec "$CONTROLLER_NAME" curl -fsS \
    -H "Authorization: Bearer ${SDN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"path_name\":\"${path_name}\",\"action_id\":${action_id},\"drone_id\":\"${drone_id}\"}" \
    http://127.0.0.1:8080/sdn/route
}

verify_installed_routes() {
  payload="$1"
  expected_1="$2"
  expected_2="$3"
  expected_3="$4"
  python3 -c '
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["installed_routes"] == {
    "drone_1": sys.argv[2],
    "drone_2": sys.argv[3],
    "drone_3": sys.argv[4],
}, payload
' "$payload" "$expected_1" "$expected_2" "$expected_3"
}

verify_cookie_output() {
  switch_name="$1"
  cookie="$2"
  expected_port="$3"
  flow_dump="$(
    docker exec "$MININET_NAME" \
      ovs-ofctl -O OpenFlow13 dump-flows "$switch_name"
  )"
  python3 -c '
import sys

cookie = sys.argv[1].lower()
expected_action = f"actions=output:{sys.argv[2]}"
matching = [line.lower() for line in sys.argv[3].splitlines() if f"cookie={cookie}" in line.lower()]
assert len(matching) == 1, matching
assert expected_action in matching[0], matching[0]
' "$cookie" "$expected_port" "$flow_dump"
}

flow_packet_count() {
  switch_name="$1"
  cookie="$2"
  flow_dump="$(
    docker exec "$MININET_NAME" \
      ovs-ofctl -O OpenFlow13 dump-flows "$switch_name"
  )"
  python3 -c '
import re
import sys

cookie = sys.argv[1].lower()
matching = [line for line in sys.argv[2].splitlines() if f"cookie={cookie}" in line.lower()]
assert matching, f"flow cookie {cookie} was not found"
print(sum(int(value) for line in matching for value in re.findall(r"n_packets=(\d+)", line)))
' "$cookie" "$flow_dump"
}

transit_packet_count() {
  switch_name="$1"
  flow_dump="$(
    docker exec "$MININET_NAME" \
      ovs-ofctl -O OpenFlow13 dump-flows "$switch_name"
  )"
  python3 -c '
import re
import sys

matching = [
    line for line in sys.argv[1].splitlines()
    if "priority=100" in line and "in_port=1" in line and "actions=output:2" in line
]
assert matching, "forward transit flow was not found"
print(sum(int(value) for line in matching for value in re.findall(r"n_packets=(\d+)", line)))
' "$flow_dump"
}

wait_for_data_plane_delivery() {
  for _attempt in $(seq 1 30); do
    ingress_1="$(flow_packet_count s1 0xf1a00001)"
    ingress_2="$(flow_packet_count s1 0xf1a00002)"
    ingress_3="$(flow_packet_count s1 0xf1a00003)"
    return_1="$(flow_packet_count s5 0xf1a00001)"
    return_2="$(flow_packet_count s5 0xf1a00002)"
    return_3="$(flow_packet_count s5 0xf1a00003)"
    direct_packets="$(transit_packet_count s2)"
    satellite_packets="$(transit_packet_count s3)"
    mesh_packets="$(transit_packet_count s4)"
    if ((
      ingress_1 > 0 && ingress_2 > 0 && ingress_3 > 0
      && return_1 > 0 && return_2 > 0 && return_3 > 0
      && direct_packets > 0 && satellite_packets > 0 && mesh_packets > 0
    )); then
      echo "PASS: live packets crossed every selected path and per-drone return flow"
      return 0
    fi
    sleep 1
  done
  echo "FAIL: OpenFlow packet counters did not advance on every selected route" >&2
  return 1
}

wait_for_drone_delivery() {
  cookie="$1"
  drone_id="$2"
  for _attempt in $(seq 1 20); do
    ingress_packets="$(flow_packet_count s1 "$cookie")"
    return_packets="$(flow_packet_count s5 "$cookie")"
    if ((ingress_packets > 0 && return_packets > 0)); then
      echo "PASS: ${drone_id} packet counters advanced after route replacement"
      return 0
    fi
    sleep 1
  done
  echo "FAIL: ${drone_id} packet counters did not advance" >&2
  return 1
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

request_route direct 0 drone_1 >"$RESULT_DIR/drone_1.json" &
route_pid_1=$!
request_route satellite 1 drone_2 >"$RESULT_DIR/drone_2.json" &
route_pid_2=$!
request_route mesh 2 drone_3 >"$RESULT_DIR/drone_3.json" &
route_pid_3=$!
wait "$route_pid_1"
wait "$route_pid_2"
wait "$route_pid_3"

direct_result="$(<"$RESULT_DIR/drone_1.json")"
satellite_result="$(<"$RESULT_DIR/drone_2.json")"
mesh_result="$(<"$RESULT_DIR/drone_3.json")"
verify_route "$direct_result" "direct"
verify_route "$satellite_result" "satellite"
verify_route "$mesh_result" "mesh"
echo "PASS: concurrent routes for all three drones were acknowledged"

flow_state="$(
  docker exec "$CONTROLLER_NAME" curl -fsS \
    -H "Authorization: Bearer ${SDN_TOKEN}" \
    http://127.0.0.1:8080/sdn/flows
)"
verify_installed_routes "$flow_state" direct satellite mesh
verify_cookie_output s1 0xf1a00001 1
verify_cookie_output s1 0xf1a00002 2
verify_cookie_output s1 0xf1a00003 3
verify_cookie_output s5 0xf1a00001 1
verify_cookie_output s5 0xf1a00002 2
verify_cookie_output s5 0xf1a00003 3
echo "PASS: controller retained independent per-drone routes"

wait_for_data_plane_delivery

docker exec "$MININET_NAME" ovs-ofctl mod-port s1 s1-eth1 down

failover_result=""
for _attempt in $(seq 1 20); do
  failover_result="$(request_route direct 0 drone_1)"
  if verify_route "$failover_result" "satellite" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
verify_route "$failover_result" "satellite"
echo "PASS: direct-link failure produced acknowledged satellite failover"

failover_state="$(
  docker exec "$CONTROLLER_NAME" curl -fsS \
    -H "Authorization: Bearer ${SDN_TOKEN}" \
    http://127.0.0.1:8080/sdn/flows
)"
verify_installed_routes "$failover_state" satellite satellite mesh
verify_cookie_output s1 0xf1a00001 2
verify_cookie_output s5 0xf1a00001 2
verify_cookie_output s1 0xf1a00002 2
verify_cookie_output s1 0xf1a00003 3
wait_for_drone_delivery 0xf1a00001 drone_1
echo "PASS: failover preserved the other drones' installed routes"

docker exec "$MININET_NAME" ovs-ofctl mod-port s1 s1-eth1 up
echo "SDN integration smoke test passed."
