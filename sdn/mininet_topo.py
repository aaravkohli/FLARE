#!/usr/bin/env python3
"""
sdn/mininet_topo.py
Custom Mininet topology for Anti-Jamming Drone swarm routing.
Sets up 5 switches:
  - s1 (Ingress Switch)
  - s2 (Direct Switch - low latency)
  - s3 (Satellite Switch - high latency)
  - s4 (Mesh Switch - medium latency)
  - s5 (Egress Switch)
And hosts loaded from config/fleet_registry.yaml at startup.
"""

import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel, info

from fleet.registry import list_drones


def disable_host_tx_checksum_offload(host):
    """Make veth UDP checksums valid through the OVS netdev datapath."""
    interface = f"{host.name}-eth0"
    output = host.cmd(f"ethtool -K {interface} tx off && echo FLARE_OFFLOAD_OK")
    if "FLARE_OFFLOAD_OK" not in output:
        raise RuntimeError(
            f"failed to disable TX checksum offload on {interface}: {output}"
        )

def setup_topology():
    import socket
    try:
        controller_ip = socket.gethostbyname('sdn-controller')
    except Exception as e:
        info(f"Warning: Failed to resolve sdn-controller, using localhost: {e}\n")
        controller_ip = '127.0.0.1'
    controller_port = 6633

    net = Mininet(topo=None, build=False, link=TCLink)

    info('*** Adding controller\n')
    c0 = net.addController(
        name='c0',
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port
    )

    info('*** Adding hosts\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
    drone_records = list_drones(enabled_only=True)
    drones = [
        net.addHost(
            f"h{record['index'] + 1}",
            ip=f"10.0.0.{record['index'] + 1}/24",
            mac=record["mac"],
        )
        for record in drone_records
    ]

    info('*** Adding switches\n')
    # Use userspace OVS switch ('user' datapath) to work inside Docker containers on macOS
    s1 = net.addSwitch('s1', dpid='0000000000000001', cls=OVSSwitch, datapath='user')
    s2 = net.addSwitch('s2', dpid='0000000000000002', cls=OVSSwitch, datapath='user')
    s3 = net.addSwitch('s3', dpid='0000000000000003', cls=OVSSwitch, datapath='user')
    s4 = net.addSwitch('s4', dpid='0000000000000004', cls=OVSSwitch, datapath='user')
    s5 = net.addSwitch('s5', dpid='0000000000000005', cls=OVSSwitch, datapath='user')

    info('*** Creating links\n')
    # Drone access ports mirror config/sdn_config.yaml.
    for record, drone in zip(drone_records, drones):
        net.addLink(drone, s1, port2=int(record["access_port"]))

    # Ingress Switch (s1) to Path Switches (s2, s3, s4)
    # s1 Port 1 -> s2 Port 1 (Direct Path: 10ms delay, 100M, no loss)
    net.addLink(s1, s2, port1=1, port2=1, delay='10ms', bw=100, loss=0)
    # s1 Port 2 -> s3 Port 1 (Satellite Path: 300ms delay, 10M, 1% loss)
    net.addLink(s1, s3, port1=2, port2=1, delay='300ms', bw=10, loss=1)
    # s1 Port 3 -> s4 Port 1 (Mesh Path: 50ms delay, 20M, 0.5% loss)
    net.addLink(s1, s4, port1=3, port2=1, delay='50ms', bw=20, loss=0.5)

    # Path Switches (s2, s3, s4) to Egress Switch (s5)
    # s2 Port 2 -> s5 Port 1 (Direct Path)
    net.addLink(s2, s5, port1=2, port2=1, delay='10ms', bw=100)
    # s3 Port 2 -> s5 Port 2 (Satellite Path)
    net.addLink(s3, s5, port1=2, port2=2, delay='300ms', bw=10)
    # s4 Port 2 -> s5 Port 3 (Mesh Path)
    net.addLink(s4, s5, port1=2, port2=3, delay='50ms', bw=20)

    # Egress Base Station host connection
    # s5 Port 4 -> h1
    net.addLink(s5, h1, port1=4)

    info('*** Starting network\n')
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])
    s3.start([c0])
    s4.start([c0])
    s5.start([c0])

    # The userspace OVS datapath cannot complete veth-generated partial UDP
    # checksums. Without this, switch counters advance but the Linux receiver
    # rejects every UDP datagram with InCsumErrors.
    for host in [h1, *drones]:
        disable_host_tx_checksum_offload(host)
    info('*** Disabled TX checksum offload on all Mininet hosts\n')

    # Let the OpenFlow handshake complete
    time.sleep(2)

    # Run iperf server in background on h1 (Base Station)
    info('*** Running iperf server on h1\n')
    h1.cmd('iperf -s -u -i 1 > /app/logs/iperf_server.log 2>&1 &')
    h1.cmd(
        'python3 sdn/control_traffic.py server '
        '--counter-file /app/logs/control_packets.json '
        '> /app/logs/control_server.log 2>&1 &'
    )
    h1.cmd(
        'python3 sdn/control_traffic.py server --port 9001 '
        '--counter-file /app/logs/data_packets.json '
        '> /app/logs/data_server.log 2>&1 &'
    )

    # Generate and monitor one traffic stream per drone.
    for record, drone in zip(drone_records, drones):
        drone_id = record["drone_id"]
        Path(f"/app/logs/namespace_pid_{drone_id}.txt").write_text(
            str(drone.pid), encoding="utf-8"
        )
        info(f'*** Starting traffic for {drone_id}\n')
        drone.cmd(
            f'iperf -c 10.0.0.1 -u -b 1M -t 999999 -i 1 '
            f'> /app/logs/iperf_{drone_id}.log 2>&1 &'
        )
        drone.cmd(
            f'ping -i 0.5 10.0.0.1 '
            f'> /app/logs/ping_{drone_id}.log 2>&1 &'
        )
        drone.cmd(
            'python3 sdn/control_traffic.py client '
            f'> /app/logs/control_{drone_id}.log 2>&1 &'
        )
        drone.cmd(
            'python3 sdn/control_traffic.py client '
            '--port 9001 --payload flare-data-probe '
            f'> /app/logs/data_{drone_id}.log 2>&1 &'
        )

    info('*** Network is running and generating traffic.\n')
    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        pass

    info('*** Stopping network\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    setup_topology()
