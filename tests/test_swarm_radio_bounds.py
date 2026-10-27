"""Radio broad-phase equivalence and current-policy route authorization."""
from copy import deepcopy
from dataclasses import asdict
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import pytest
import yaml

from simulation.swarm.engine import SwarmEngine
from simulation.swarm.network import Link, radio_link
from simulation.swarm.terrain import Terrain
from simulation.swarm.topology import pair


def configuration():
    return yaml.safe_load(Path('config/swarm_config.yaml').read_text())


def test_terrain_ceiling_bounds_grid_blend_and_extension():
    t=Terrain();g=t.grid
    for iz in range(g['rows']):
        for ix in range(g['columns']):
            x=(ix/(g['columns']-1)-.5)*g['worldWidthM']
            z=(iz/(g['rows']-1)-.5)*g['worldDepthM']
            assert t.height(x,z)<=t._height_ceiling
    rng=random.Random(42)
    for _ in range(10000):
        assert t.height(rng.uniform(-4000,4000),rng.uniform(-4000,4000))<=t._height_ceiling
    assert not t.clears_heightfield(t._height_ceiling+3,3)
    assert t.clears_heightfield(math.nextafter(t._height_ceiling+3,math.inf),3)


def test_proved_clear_radio_path_skips_samples_and_low_path_keeps_all_eight():
    t=Terrain();cfg=configuration()['radio'];cfg['range_m']=1000
    calls=[];previous=sys.getprofile()
    def profile(frame,event,arg):
        if event=='call' and frame.f_code is Terrain.height.__code__:calls.append(frame)
    sys.setprofile(profile)
    try:
        assert radio_link('a','b',[0,80,0],[100,90,0],cfg,t,[])
        assert not calls
        assert radio_link('a','b',[0,10,0],[100,15,0],cfg,t,[])
        assert len(calls)==8
    finally:sys.setprofile(previous)


@pytest.mark.parametrize('seed',[7,42,99])
def test_radio_outputs_match_full_sampling_at_high_low_and_threshold_paths(seed):
    rng=random.Random(seed);t=Terrain();reference=SimpleNamespace(height=t.height)
    cfg=configuration()['radio'];cfg['range_m']=2000
    for i in range(3000):
        p=[rng.uniform(-3500,3500),rng.uniform(-30,120),rng.uniform(-3500,3500)]
        q=[p[0]+rng.uniform(-400,400),rng.uniform(-30,120),p[2]+rng.uniform(-400,400)]
        if i%4==0:p[1]=q[1]=rng.choice((t._height_ceiling+3,math.nextafter(t._height_ceiling+3,math.inf),80.))
        jammers=[] if i%3 else [dict(position=[p[0]+30,0,p[2]-20],radius_m=250,power_dbm=-30)]
        args=('a','b',p,q,cfg);health=rng.choice((0.,.2,.7,1.));offset=rng.uniform(-5,5)
        optimized=radio_link(*args,t,jammers,health,offset)
        sampled=radio_link(*args,reference,jammers,health,offset)
        assert (asdict(optimized) if optimized else None)==(asdict(sampled) if sampled else None)


def test_custom_terrain_and_sampler_changes_cannot_reuse_clearance_certificate(monkeypatch,tmp_path):
    cfg=configuration()['radio'];cfg['range_m']=1000;t=Terrain()
    assert t.clears_heightfield(80,3)
    monkeypatch.setattr(t,'height',lambda x,z:100.)
    assert not t.clears_heightfield(80,3)
    custom=radio_link('a','b',[0,80,0],[100,80,0],cfg,t,[])
    sampled=radio_link('a','b',[0,80,0],[100,80,0],cfg,SimpleNamespace(height=t.height),[])
    assert asdict(custom)==asdict(sampled)
    monkeypatch.setattr(Terrain,'height',lambda self,x,z:100.)
    other=Terrain();assert not other.clears_heightfield(80,3)
    fallback=Terrain(tmp_path/'missing-heightmap.ts');assert not fallback.samples
    assert not fallback.clears_heightfield(80,3)


def test_representative_fallback_matches_explicit_sampling(tmp_path):
    t=Terrain(tmp_path/'missing-heightmap.ts');reference=SimpleNamespace(height=t.height)
    assert t.provenance['fallback'] and t.clears_heightfield(80,3)
    cfg=configuration()['radio'];cfg['range_m']=2000;rng=random.Random(7)
    for _ in range(500):
        p=[rng.uniform(-1500,1500),rng.uniform(10,100),rng.uniform(-1500,1500)]
        q=[p[0]+100,rng.uniform(10,100),p[2]+100]
        assert asdict(radio_link('a','b',p,q,cfg,t,[]))==asdict(radio_link('a','b',p,q,cfg,reference,[]))


def test_edge_canonicalization_preserves_sort_order_and_equal_identity():
    class Identity(str):pass
    values=['base']+[f'UAV-{n:03}' for n in range(1,101)]+['','é','🚁']
    for a in values:
        for b in values:assert pair(a,b)==tuple(sorted((a,b)))
    a,b=Identity('equal'),Identity('equal')
    assert pair(a,b)[0] is a and pair(a,b)[1] is b


def original_valid_route(e,hops,family,control=False):
    """Prior authoritative rule; intentionally retains repeated edge work."""
    if family=='hold':return False
    if len(hops)<2 or len(set(hops))!=len(hops) or 'base' in hops[1:-1]:return False
    if family in e.failed_families:return False
    for identity in hops:
        d=e.nodes.get(identity)
        if d and (not d.available or d.health<=0 or (d.threat or 0)>.8 or d.containment=='quarantined' or ((d.containment in ('restricted','control_only') or e.families.get(identity)=='hold') and not control)):return False
    if family=='satellite':return hops[-1]=='base' and len(hops)==2 and e.satellite_link(hops[0]).loss<=e.cfg['radio']['max_loss']
    if family=='direct':return len(hops)==2 and pair(*hops) not in e.blocked and (link:=e.attacks.impair(e.radio_links.get(pair(*hops)),family,hops)) is not None and link.loss<=e.cfg['radio']['max_loss']
    return all(pair(a,b) in e.links and pair(a,b) not in e.blocked|e.auto_blocked and e.links[pair(a,b)].loss<=e.cfg['radio']['max_loss'] for a,b in zip(hops,hops[1:]))


def test_route_checks_match_original_rules_and_recheck_changed_security():
    cfg=configuration();cfg.update(default_count=10,topology_mode='manual',topology='full_mesh')
    e=SwarmEngine([],cfg,isolated=True);ids=list(e.nodes);rng=random.Random(99)
    for _ in range(2000):
        a,b=rng.sample(ids,2);edge=pair(a,b)
        for identity in ids:
            d=e.nodes[identity];d.available=True;d.health=1.;d.threat=0.;d.containment='normal'
        e.families.clear();e.failed_families.clear();e.blocked.clear();e.auto_blocked.clear()
        e.links={edge:Link(a,b,1,-40,30,12,rng.choice((0.,.35,.36,float('nan'))),1)}
        e.radio_links=deepcopy(e.links)
        condition=rng.randrange(8)
        if condition==0:e.nodes[a].containment='quarantined'
        if condition==1:e.nodes[b].containment='control_only'
        if condition==2:e.families[b]='hold'
        if condition==3:e.nodes[a].available=False
        if condition==4:e.blocked.add(edge)
        if condition==5:e.auto_blocked.add(edge)
        if condition==6:e.nodes[b].threat=.9
        for control in (False,True):
            for family in ('mesh','direct','satellite','hold'):
                for hops in ([a,b],[a,'base'],[a,b,'base'],[a,'base',b],[a,a]):
                    assert e.valid_route(hops,family,control)==original_valid_route(e,hops,family,control)
    e.nodes[a].containment=e.nodes[b].containment='normal';e.nodes[a].available=e.nodes[b].available=True
    e.nodes[a].threat=e.nodes[b].threat=0.;e.families.clear();e.failed_families.clear();e.blocked.clear();e.auto_blocked.clear()
    e.links={edge:Link(a,b,1,-40,30,12,0,1)}
    assert e.valid_route([a,b],'mesh')
    e.blocked.add(edge);assert not e.valid_route([a,b],'mesh')
    e.blocked.clear();e.nodes[b].containment='quarantined';assert not e.valid_route([a,b],'mesh')
