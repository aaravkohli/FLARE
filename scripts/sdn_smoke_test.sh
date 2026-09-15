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
controller_mounts=(
  -v "$PROJECT_ROOT/config:/app/config:ro"
  -v "$PROJECT_ROOT/fleet:/app/fleet:ro"
)
mininet_mounts=(
  -v "$PROJECT_ROOT/config:/app/config:ro"
  -v "$PROJECT_ROOT/fleet:/app/fleet:ro"
)

if [[ "${FLARE_BIND_SOURCE:-0}" == "1" ]]; then
  controller_mounts+=(-v "$PROJECT_ROOT/sdn:/app/sdn:ro")
  mininet_mounts+=(-v "$PROJECT_ROOT/sdn:/app/sdn:ro")
fi

cleanup() {
  exit_code=$?
  trap - EXIT
  if [[ $exit_code -ne 0 ]]; then
    docker logs "$CONTROLLER_NAME" 2>/dev/null || true
    docker logs "$MININET_NAME" 2>/dev/null || true
    if [[ "${FLARE_KEEP_ON_FAILURE:-0}" == "1" ]]; then
      echo "Preserved failing gate for diagnosis:" >&2
      echo "  controller=${CONTROLLER_NAME}" >&2
      echo "  mininet=${MININET_NAME}" >&2
      echo "  network=${NETWORK_NAME}" >&2
      echo "  volume=${LOG_VOLUME}" >&2
      echo "  result_dir=${RESULT_DIR}" >&2
      exit "$exit_code"
    fi
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

wait_for_topology() {
  for _attempt in $(seq 1 60); do
    if docker exec "$CONTROLLER_NAME" curl -fsS http://127.0.0.1:8080/ready \
      >/dev/null 2>&1; then
      echo "PASS: five-switch OpenFlow topology readiness"
      return 0
    fi

    if [[ "$(docker inspect --format '{{.State.Running}}' "$MININET_NAME" 2>/dev/null || true)" != "true" ]]; then
      echo "FAIL: Mininet container exited before the topology became ready" >&2
      return 1
    fi

    sleep 1
  done

  echo "FAIL: timed out waiting for five-switch OpenFlow topology readiness" >&2
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

request_containment() {
  mode="$1"
  drone_id="$2"
  docker exec "$CONTROLLER_NAME" curl -fsS \
    -H "Authorization: Bearer ${SDN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"drone_id\":\"${drone_id}\",\"mode\":\"${mode}\",\"reason\":\"packet-validation\"}" \
    http://127.0.0.1:8080/sdn/containment
}

verify_containment() {
  payload="$1"
  expected_mode="$2"
  python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload["status"] == "ok", payload
assert payload["mode"] == sys.argv[2], payload
assert payload["acknowledged_switches"] == [1, 2, 3, 4, 5], payload
' "$payload" "$expected_mode"
}

verify_controller_evidence() {
  payload="$1"
  python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload["available"] is True, payload
assert payload["source"] == "ryu_openflow", payload
assert payload["independent"] is True, payload
assert payload["controller_rx_packets"] > 0, payload
assert "packet_counters" in payload["supported_signals"], payload
assert payload["control_rate_observer"] == "access_port_packet_in", payload
assert payload["drop_counter_semantics"] == "ingress_port_receive_drop_error", payload
assert "port_receive_drop_counters" in payload["supported_signals"], payload
assert payload["controller_dropped_packets"] == (
    payload["controller_port_dropped_packets"]
    + payload["controller_port_error_packets"]
), payload
' "$payload"
}

wait_for_port_evidence() {
  for _attempt in $(seq 1 30); do
    port_evidence="$(
      docker exec "$CONTROLLER_NAME" curl -fsS \
        -H "Authorization: Bearer ${SDN_TOKEN}" \
        http://127.0.0.1:8080/sdn/evidence/drone_1
    )"
    if python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("drop_counter_semantics") == "ingress_port_receive_drop_error", payload
assert payload.get("port_timestamp") is not None, payload
' "$port_evidence" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  echo "FAIL: Ryu did not return ingress port drop/error statistics" >&2
  return 1
}

wait_for_policy_drop_evidence() {
  baseline_port_dropped="$1"
  baseline_port_errors="$2"
  for _attempt in $(seq 1 30); do
    drop_evidence="$(
      docker exec "$CONTROLLER_NAME" curl -fsS \
        -H "Authorization: Bearer ${SDN_TOKEN}" \
        http://127.0.0.1:8080/sdn/evidence/drone_1
    )"
    if python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload["controller_policy_dropped_packets"] > 0, payload
assert payload["controller_dropped_packets"] == (
    payload["controller_port_dropped_packets"]
    + payload["controller_port_error_packets"]
), payload
assert payload["drop_counter_semantics"] == "ingress_port_receive_drop_error", payload
assert payload["controller_port_dropped_packets"] == int(sys.argv[2]), payload
assert payload["controller_port_error_packets"] == int(sys.argv[3]), payload
' "$drop_evidence" "$baseline_port_dropped" "$baseline_port_errors" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  echo "FAIL: Ryu policy drops were not separate from port receive drops" >&2
  return 1
}

wait_for_packet_in_evidence() {
  expected_mac="$1"
  for _attempt in $(seq 1 30); do
    packet_in_evidence="$(
      docker exec "$CONTROLLER_NAME" curl -fsS \
        -H "Authorization: Bearer ${SDN_TOKEN}" \
        http://127.0.0.1:8080/sdn/evidence/drone_1
    )"
    if python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload["observed_source_mac"] == sys.argv[2], payload
assert payload["identity_observed_at"] is not None, payload
assert payload["control_messages_per_s"] > 0, payload
assert payload["control_rate_observer"] == "access_port_packet_in", payload
' "$packet_in_evidence" "$expected_mac" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.05
  done
  echo "FAIL: Ryu did not observe participant-bound PacketIn identity/rate" >&2
  return 1
}

verify_identity_binding() {
  drone_id="$1"
  switch_name="$2"
  expected="$(
    docker exec "$CONTROLLER_NAME" python -c \
      "from fleet.registry import get_drone; d=get_drone('${drone_id}'); print(d['mac'], d['access_port'])"
  )"
  expected="${expected//$'\n'/}"
  expected_mac="${expected% *}"
  expected_port="${expected##* }"
  flow_dump="$(docker exec "$MININET_NAME" ovs-ofctl -O OpenFlow13 dump-flows "$switch_name")"
  python3 -c '
import sys
mac, port, flow_dump = sys.argv[1:]
lower = flow_dump.lower()
assert f"dl_src={mac.lower()}" in lower, flow_dump
assert any(f"dl_dst={mac.lower()}" in line and f"actions=output:{port}" in line for line in lower.splitlines()), flow_dump
' "$expected_mac" "$expected_port" "$flow_dump"
}

wait_for_containment_packets() {
  for _attempt in $(seq 1 20); do
    dump="$(docker exec "$MININET_NAME" ovs-ofctl -O OpenFlow13 dump-flows s1)"
    if python3 -c '
import re, sys
lines = [
    line.lower() for line in sys.argv[1].splitlines()
    if "cookie=0xf1b00001" in line.lower()
    and "priority=290" in line.lower()
    and "actions=drop" in line.lower()
]
assert lines
assert any(int(value) > 0 for line in lines for value in re.findall(r"n_packets=(\d+)", line))
' "$dump" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "FAIL: containment rules did not observe packets" >&2
  return 1
}

receiver_packet_count() {
  drone_id="$1"
  counter_file="$2"
  docker exec "$MININET_NAME" python3 -c '
import json
from pathlib import Path
from fleet.registry import get_drone
import sys
path = Path("/app/logs") / sys.argv[2]
drone = get_drone(sys.argv[1])
assert drone is not None, sys.argv[1]
source_ip = "10.0.0." + str(drone["index"] + 1)
payload = json.loads(path.read_text()) if path.is_file() else {}
print(payload.get("received_by_source_ip", {}).get(source_ip, 0))
' "$drone_id" "$counter_file"
}

control_packet_count() {
  receiver_packet_count "$1" control_packets.json
}

data_packet_count() {
  receiver_packet_count "$1" data_packets.json
}

wait_for_control_delivery_after() {
  baseline="$1"
  drone_id="$2"
  for _attempt in $(seq 1 20); do
    current="$(control_packet_count "$drone_id")"
    if ((current > baseline)); then
      return 0
    fi
    sleep 0.25
  done
  echo "FAIL: configured control traffic was not delivered for ${drone_id}" >&2
  return 1
}

wait_for_data_delivery_after() {
  baseline="$1"
  drone_id="$2"
  for _attempt in $(seq 1 20); do
    current="$(data_packet_count "$drone_id")"
    if ((current > baseline)); then
      return 0
    fi
    sleep 0.25
  done
  echo "FAIL: data traffic was not delivered to the receiver for ${drone_id}" >&2
  return 1
}

assert_no_data_delivery() {
  drone_id="$1"
  sleep 1
  baseline="$(data_packet_count "$drone_id")"
  sleep 2
  current="$(data_packet_count "$drone_id")"
  if ((current != baseline)); then
    echo "FAIL: blocked data traffic reached the receiver for ${drone_id}" >&2
    return 1
  fi
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
expected = {
    "drone_1": sys.argv[2],
    "drone_2": sys.argv[3],
    "drone_3": sys.argv[4],
}
assert all(payload["installed_routes"].get(drone_id) == route for drone_id, route in expected.items()), payload
' "$payload" "$expected_1" "$expected_2" "$expected_3"
}

verify_installed_route_for() {
  payload="$1"
  drone_id="$2"
  expected_path="$3"
  python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
assert payload["installed_routes"].get(sys.argv[2]) == sys.argv[3], payload
' "$payload" "$drone_id" "$expected_path"
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

wait_for_topology

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
for routed_drone_id in drone_1 drone_2 drone_3; do
  data_baseline="$(data_packet_count "$routed_drone_id")"
  wait_for_data_delivery_after "$data_baseline" "$routed_drone_id"
done
echo "PASS: receiver observed data packets from direct, satellite, and mesh drones"
control_baseline="$(control_packet_count drone_1)"
wait_for_control_delivery_after "$control_baseline" drone_1
echo "PASS: baseline control UDP reached the receiver from drone_1"

while IFS= read -r extra_drone_id; do
  [[ -n "$extra_drone_id" ]] || continue
  case "$extra_drone_id" in
    drone_1|drone_2|drone_3) continue ;;
  esac
  extra_record="$(
    docker exec "$CONTROLLER_NAME" python -c \
      "from fleet.registry import get_drone; d=get_drone('${extra_drone_id}'); print(d['index'])"
  )"
  extra_cookie="$(printf '0xf1a%05x' "$extra_record")"
  extra_result="$(request_route mesh 2 "$extra_drone_id")"
  verify_route "$extra_result" mesh
  verify_identity_binding "$extra_drone_id" s1
  verify_cookie_output s1 "$extra_cookie" 3
  verify_cookie_output s5 "$extra_cookie" 3
  wait_for_drone_delivery "$extra_cookie" "$extra_drone_id"
  extra_data_before="$(data_packet_count "$extra_drone_id")"
  wait_for_data_delivery_after "$extra_data_before" "$extra_drone_id"
  extra_control_before="$(control_packet_count "$extra_drone_id")"
  wait_for_control_delivery_after "$extra_control_before" "$extra_drone_id"
  echo "PASS: fleet-enrolled ${extra_drone_id} has distinct MAC/access-port binding and live delivery"
done < <(
  docker exec "$CONTROLLER_NAME" python -c \
    'from fleet.registry import active_drone_ids; print("\n".join(active_drone_ids()))'
)

verify_identity_binding drone_1 s1
echo "PASS: registry MAC and access-port binding is present in OpenFlow rules"

wait_for_port_evidence
evidence="$(
  docker exec "$CONTROLLER_NAME" curl -fsS \
    -H "Authorization: Bearer ${SDN_TOKEN}" \
    http://127.0.0.1:8080/sdn/evidence/drone_1
)"
verify_controller_evidence "$evidence"
baseline_port_dropped="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["controller_port_dropped_packets"])' "$evidence")"
baseline_port_errors="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["controller_port_error_packets"])' "$evidence")"
echo "PASS: authenticated controller evidence changed with generated traffic"

namespace_pid="$(
  docker exec "$MININET_NAME" cat /app/logs/namespace_pid_drone_1.txt
)"
docker exec "$MININET_NAME" nsenter -t "$namespace_pid" -n \
  python3 sdn/packet_in_probe.py --interface h2-eth0 \
  --source-mac 02:aa:00:00:00:01 --count 12
wait_for_packet_in_evidence 02:aa:00:00:00:01
echo "PASS: controller observed access-port PacketIn rate and actual spoofed source MAC"

hold_result="$(request_route hold 3 drone_1)"
verify_route "$hold_result" hold
wait_for_containment_packets
assert_no_data_delivery drone_1
wait_for_policy_drop_evidence "$baseline_port_dropped" "$baseline_port_errors"
echo "PASS: HOLD installed an acknowledged packet-drop rule"
echo "PASS: policy-ordered drops are not port drop/error evidence"

normal_result="$(request_containment normal drone_1)"
verify_containment "$normal_result" normal
request_route direct 0 drone_1 >/dev/null
wait_for_drone_delivery 0xf1a00001 drone_1
normal_data_before="$(data_packet_count drone_1)"
wait_for_data_delivery_after "$normal_data_before" drone_1
echo "PASS: route-level HOLD was reversibly replaced"

restricted_result="$(request_containment restricted drone_1)"
verify_containment "$restricted_result" restricted
wait_for_containment_packets
assert_no_data_delivery drone_1
restricted_control_before="$(control_packet_count drone_1)"
wait_for_control_delivery_after "$restricted_control_before" drone_1
restricted_dump="$(docker exec "$MININET_NAME" ovs-ofctl -O OpenFlow13 dump-flows s1)"
python3 -c '
import sys
lines = [line.lower() for line in sys.argv[1].splitlines() if "cookie=0xf1b00001" in line.lower()]
assert any("tp_dst=9000" in line and "actions=output:1" in line for line in lines), lines
assert any("actions=drop" in line for line in lines), lines
' "$restricted_dump"
echo "PASS: restricted mode drops data and delivers drone_1 control UDP"

control_only_result="$(request_containment control_only drone_1)"
verify_containment "$control_only_result" control_only
wait_for_containment_packets
assert_no_data_delivery drone_1
control_only_before="$(control_packet_count drone_1)"
wait_for_control_delivery_after "$control_only_before" drone_1
echo "PASS: control-only mode drops data and delivers drone_1 control UDP"

quarantine_result="$(request_containment quarantined drone_1)"
verify_containment "$quarantine_result" quarantined
assert_no_data_delivery drone_1
sleep 1
quarantine_control_before="$(control_packet_count drone_1)"
sleep 2
quarantine_control_after="$(control_packet_count drone_1)"
if ((quarantine_control_after != quarantine_control_before)); then
  echo "FAIL: quarantine allowed control traffic to reach the receiver" >&2
  exit 1
fi
quarantine_dump="$(docker exec "$MININET_NAME" ovs-ofctl -O OpenFlow13 dump-flows s1)"
python3 -c '
import sys
lines = [line.lower() for line in sys.argv[1].splitlines() if "cookie=0xf1b00001" in line.lower()]
assert lines and all("tp_dst=9000" not in line for line in lines), lines
assert any("actions=drop" in line for line in lines), lines
' "$quarantine_dump"
echo "PASS: quarantine blocks both data and control traffic"

verify_containment "$(request_containment normal drone_1)" normal
request_route direct 0 drone_1 >/dev/null

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
while IFS= read -r extra_drone_id; do
  [[ -n "$extra_drone_id" ]] || continue
  case "$extra_drone_id" in
    drone_1|drone_2|drone_3) continue ;;
  esac
  verify_installed_route_for "$failover_state" "$extra_drone_id" mesh
done < <(
  docker exec "$CONTROLLER_NAME" python -c \
    'from fleet.registry import active_drone_ids; print("\n".join(active_drone_ids()))'
)
verify_cookie_output s1 0xf1a00001 2
verify_cookie_output s5 0xf1a00001 2
verify_cookie_output s1 0xf1a00002 2
verify_cookie_output s1 0xf1a00003 3
wait_for_drone_delivery 0xf1a00001 drone_1
failover_data_before="$(data_packet_count drone_1)"
wait_for_data_delivery_after "$failover_data_before" drone_1
echo "PASS: failover preserved the other drones' installed routes"

docker exec "$MININET_NAME" ovs-ofctl mod-port s1 s1-eth1 up
echo "SDN integration smoke test passed."
