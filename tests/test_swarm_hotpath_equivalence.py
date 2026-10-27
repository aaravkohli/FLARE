"""Independent pre-optimization contracts for terrain and wire snapshots."""
from dataclasses import asdict
import math
from pathlib import Path
import random
import struct

import pytest

from simulation.swarm.network import Link, Packet, PacketScheduler
from simulation.swarm.terrain import Terrain, representative


def original_height(terrain, x, z):
    """The committed interpolator before the performance change, kept independent."""
    g = terrain.grid
    if not terrain.samples or abs(x) >= g['worldWidthM']/2 or abs(z) >= g['worldDepthM']/2:
        return representative(x, z)
    u = (x/g['worldWidthM']+.5)*(g['columns']-1)
    v = (z/g['worldDepthM']+.5)*(g['rows']-1)
    ix, iz = int(u), int(v)
    fx, fz = u-ix, v-iz
    def sample(cx, cz):
        return g['elevationBaseM']+terrain.samples[cz*g['columns']+cx]*g['elevationStepM']
    elevation = (sample(ix,iz)*(1-fx)+sample(ix+1,iz)*fx)*(1-fz)+(sample(ix,iz+1)*(1-fx)+sample(ix+1,iz+1)*fx)*fz
    core = elevation-g['maximumElevationM']+g['maxWorldHeightM']
    edge = min(g['worldWidthM']/2-abs(x), g['worldDepthM']/2-abs(z))
    blend = min(1, max(0, edge/g['edgeBlendM']))
    blend = blend*blend*(3-2*blend)
    return core*blend+representative(x,z)*(1-blend)


def test_terrain_is_bit_identical_including_grid_and_blend_boundaries(tmp_path):
    terrain = Terrain()
    assert not terrain.provenance['fallback']
    g = terrain.grid
    rng = random.Random(42)
    points = [(rng.uniform(-g['worldWidthM'], g['worldWidthM']),
               rng.uniform(-g['worldDepthM'], g['worldDepthM'])) for _ in range(20000)]
    for width, count in ((g['worldWidthM'], g['columns']), (g['worldDepthM'], g['rows'])):
        for i in range(count):
            v = (i/(count-1)-.5)*width
            for delta in (-1e-9, 0., 1e-9):
                points.extend(((v+delta, 0.), (0., v+delta)))
        for v in (-width/2, -width/2+g['edgeBlendM'], 0., width/2-g['edgeBlendM'], width/2):
            points.extend(((math.nextafter(v,-math.inf), 0.), (v,0.), (math.nextafter(v,math.inf),0.)))
    samples = terrain.samples
    for x,z in points:
        assert struct.pack('d',terrain.height(x,z)) == struct.pack('d',original_height(terrain,x,z))
    assert terrain.samples is samples
    missing = Terrain(tmp_path/'missing-heightfield.ts')
    assert missing.provenance['fallback'] and not missing._elevations
    for x,z in points[::100]:
        assert missing.height(x,z) == representative(x,z)


@pytest.mark.parametrize('category', ['sensor','command','ack','fl_update','threat'])
def test_packet_snapshots_preserve_wire_values_and_deep_payload_isolation(category):
    p = Packet('p1','a','c',category,1024,['a','b','c'],.05,20.05,7,
               service_kind='registered',service_deadline=2.,channel_key='logical:1',
               restrictions={'excluded_nodes':['z'],'nested':{'values':[1,None,0.]}},
               history=[dict(from_node='a',to_node='b',start_at=.05,end_at=.06,attempt=1)])
    expected = dict(asdict(p),protocol='reliable_simulated' if category in ('command','ack','fl_update') else 'udp_simulated')
    row = p.snapshot()
    assert list(row) == list(expected)
    assert row == expected
    row['route'].append('z');row['restrictions']['nested']['values'].append('changed')
    row['history'][0]['attempt'] = 99
    assert p.route == ['a','b','c'] and p.restrictions['nested']['values'] == [1,None,0.]
    assert p.history[0]['attempt'] == 1
    p.service_kind = p.service_deadline = p.channel_key = None
    assert all(key not in p.snapshot() for key in ('service_kind','service_deadline','channel_key'))


def test_every_actual_scheduler_event_matches_reference_packet_serialization():
    events = []
    s = None
    def event(kind, row):
        p = s.packets.get(row['id'])
        if p:
            expected = asdict(p)
            for key in ('service_kind','service_deadline','channel_key'):
                if expected[key] is None: expected.pop(key)
            assert all(row[key] == value for key,value in expected.items())
        events.append((kind,row))
    s = PacketScheduler(dict(ttl_s=3,retries=3,capacity=100,queue_limit=64,seed=99),random.Random(99),event)
    links = {('a','b'):Link('a','b',1,-40,30,.5,.25,3),('b','c'):Link('b','c',1,-40,30,.5,.25,3)}
    for link in links.values():
        assert link.snapshot() == dict(asdict(link),id=':'.join(sorted((link.a,link.b))),encrypted='simulated_policy')
    for i in range(20):s.emit('a','c','command',512,['a','b','c'],0,0)
    s.step(3,links,lambda *_:True)
    assert s.generated == s.delivered+s.dropped == 20 and any(k=='packet_retry' for k,_ in events)
    assert any(k=='packet_hop_completed' for k,_ in events)
