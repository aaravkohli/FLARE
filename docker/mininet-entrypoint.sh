#!/usr/bin/env bash
set -euo pipefail

OVS_RUN_DIR="/var/run/openvswitch"
OVS_DB_DIR="/etc/openvswitch"
OVS_LOG_DIR="/var/log/openvswitch"
OVS_DB="${OVS_DB_DIR}/conf.db"
OVS_SCHEMA="/usr/share/openvswitch/vswitch.ovsschema"

# The topology uses OVS's netdev datapath, which needs TUN/TAP but not the
# host's openvswitch kernel module. Starting the distribution service would
# still try to modprobe that optional module and fails on GitHub-hosted runners.
if [[ ! -c /dev/net/tun ]]; then
  echo "ERROR: /dev/net/tun is unavailable; the Mininet userspace datapath cannot start." >&2
  echo "Run the container with --privileged on a Linux host with TUN/TAP support." >&2
  exit 1
fi

mkdir -p "$OVS_RUN_DIR" "$OVS_DB_DIR" "$OVS_LOG_DIR"

if [[ ! -f "$OVS_DB" ]]; then
  ovsdb-tool create "$OVS_DB" "$OVS_SCHEMA"
fi

ovsdb-server "$OVS_DB" \
  --remote="punix:${OVS_RUN_DIR}/db.sock" \
  --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
  --pidfile="${OVS_RUN_DIR}/ovsdb-server.pid" \
  --detach \
  --log-file="${OVS_LOG_DIR}/ovsdb-server.log"

ovs-vsctl --no-wait init

ovs-vswitchd "unix:${OVS_RUN_DIR}/db.sock" \
  --pidfile="${OVS_RUN_DIR}/ovs-vswitchd.pid" \
  --detach \
  --log-file="${OVS_LOG_DIR}/ovs-vswitchd.log"

exec python3 sdn/mininet_topo.py
