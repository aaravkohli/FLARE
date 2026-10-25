"""Counterfactual graph policy, simulation-clock stability and packet-safe transitions."""
from copy import deepcopy
import json
from pathlib import Path
import random

import pytest
import yaml
from pydantic import ValidationError

from schemas.mission import MissionConfig, MissionControl, MissionAttack, ExperimentRequest, TOPOLOGIES
from simulation.swarm.adaptive_topology import AdaptivePolicy, Candidate, connectivity_state, connectivity_from_adjacencies, paths_from, paths_to_base
from simulation.swarm.engine import SwarmEngine, RevisionConflict
from simulation.swarm.experiments import reference_engine
from simulation.swarm.network import Link, PacketScheduler, build_adjacency, shortest_path
from simulation.swarm.topology import pair


def configuration(**changes):
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg.update(dict(default_count=10,topology_mode='manual')|changes)
    return cfg


def engine(**changes):
    return SwarmEngine([],configuration(**changes),isolated=True)


def run(e,seconds):
    e.playing=True
    for _ in range(round(seconds/.05)): e.tick(.05)


def complete(e,bandwidth=12):
    ids=list(e.nodes)
    e.physical_links={pair(a,b):Link(a,b,1,-40,35,bandwidth,0,1) for i,a in enumerate(ids) for b in ids[i+1:]}
    return ids


@pytest.mark.parametrize('seed',[7,42,99])
@pytest.mark.parametrize('count',range(10,101,10))
def test_all_ten_candidates_size_seed_matrix_is_deterministic_and_isolated(count,seed):
    e=engine(default_count=count,seed=seed)
    before=deepcopy((e.nodes,e.topology_state,e.links,e.flows,e.forwarding,e.cfg,e.scheduler.serial,e.rng.getstate()))
    e.adaptive.evaluate(force=True)
    first=[c.diagnostic() for c in e.adaptive.candidates.values()]
    e.adaptive.evaluate(force=True)
    assert first==[c.diagnostic() for c in e.adaptive.candidates.values()]
    assert len(first)==10
    assert before==(e.nodes,e.topology_state,e.links,e.flows,e.forwarding,e.cfg,e.scheduler.serial,e.rng.getstate())
    for c in e.adaptive.candidates.values():
        assert c.edges<=e.physical_links.keys()
        assert 0<=c.score<=1 and 0<=c.penalty<=.05
        assert all(0<=factor<=1+1e-12 for factor in c.factors.values())
        assert not any('base' in edge for edge in c.edges)


def test_validated_policy_and_mode_contract():
    with pytest.raises(ValidationError): AdaptivePolicy(weights={'reliability':1})
    with pytest.raises(ValidationError): AdaptivePolicy(drain_s=2)
    with pytest.raises(ValidationError): MissionConfig(expected_revision=1,topology_mode='random')
    e=engine(); assert e.topology_mode=='manual'
    default=SwarmEngine([],isolated=True)
    assert default.topology_mode=='auto' and default.adaptive.last_evaluation==0
    assert default.topology in TOPOLOGIES
    assert default.adaptive.snapshot()['evidence']=='nominal_schedule_estimate'
    default.configure(MissionConfig(expected_revision=default.revision,topology='chain'))
    assert default.topology_mode=='manual' and default.topology=='chain'


def test_score_matches_independent_lossless_calculation_and_family_measurements():
    e=engine(); ids=complete(e); e.cfg['packets']['jitter_ms']=0
    rate={(ids[1],ids[2],'sensor'):512}
    c=e.adaptive.candidate('full_mesh',e.permitted_physical_links(),rate,{})
    delay=1+512*8/(12*1000)
    assert c.factors['reliability']==1
    assert c.factors['latency']==pytest.approx(1/(1+delay/50))
    assert c.factors['capacity']==1
    assert c.factors['efficiency']==pytest.approx(1-(45/80)/2)
    assert c.score==pytest.approx(.35+.25/(1+delay/50)+.25+.15*(1-45/160))
    e.adaptive.offer(ids[1],ids[2],'sensor',512,0)
    edge=pair(ids[1],ids[2])
    e.scheduler.channel_observations.extend((0,'direct',edge,False,None) for _ in range(20))
    e.adaptive.evaluate(True)
    assert e.adaptive.candidates['full_mesh'].factors['reliability']==1
    e.scheduler.channel_observations.extend((0,'mesh',edge,False,None) for _ in range(7))
    e.adaptive.evaluate(True)
    assert e.adaptive.candidates['full_mesh'].factors['reliability']==1
    e.scheduler.channel_observations.append((0,'mesh',edge,False,None))
    e.adaptive.evaluate(True)
    c=e.adaptive.candidates['full_mesh']
    assert c.factors['reliability']==0 and c.observed_edges==1
    e.seconds=10.01; e.adaptive.offer(ids[1],ids[2],'sensor',512,e.seconds); e.adaptive.evaluate(True)
    assert e.adaptive.candidates['full_mesh'].factors['reliability']==1


def test_congestion_and_low_battery_change_capacity_and_relay_cost():
    e=engine(); ids=complete(e,bandwidth=.05)
    rates={(a,b,'sensor'):512 for a in ids for b in ids if a!=b}
    c=e.adaptive.candidate('star',e.permitted_physical_links(),rates,{})
    full=e.adaptive.candidate('full_mesh',e.permitted_physical_links(),rates,{})
    assert full.factors['capacity']>c.factors['capacity']
    assert full.score>c.score
    e.nodes[c.heads[0]].battery=.01
    low=e.adaptive.candidate('star',e.permitted_physical_links(),{(ids[1],ids[2],'sensor'):512},{})
    e.nodes[c.heads[0]].battery=1
    high=e.adaptive.candidate('star',e.permitted_physical_links(),{(ids[1],ids[2],'sensor'):512},{})
    assert low.factors['efficiency']<high.factors['efficiency']


def test_partition_policy_hold_and_base_never_create_peer_transit():
    e=engine(); ids=complete(e)
    e.physical_links={edge:link for edge,link in e.physical_links.items() if (edge[0] in ids[:5])==(edge[1] in ids[:5])}
    for n in ids: e.physical_links[pair(n,'base')]=Link(n,'base',1,-40,35,12,0,1)
    e.nodes[ids[2]].containment='quarantined';e.nodes[ids[3]].selected_route='hold'
    e.blocked.add(pair(ids[0],ids[1]))
    e.adaptive.evaluate(True)
    for c in e.adaptive.candidates.values():
        assert pair(ids[0],ids[8]) not in c.data_pairs
        assert all(ids[2] not in edge and ids[3] not in edge for edge in c.data_pairs)
    pairs,critical=connectivity_state({pair(n,'base'):Link(n,'base',1,-40,35,12,0,1) for n in ids},e.nodes,set())
    assert not pairs and critical


@pytest.mark.parametrize('topology',['ring','chain','manet','star'])
def test_manual_override_legacy_scenario_reset_and_reenable(topology):
    e=engine(); positions=[n.position[:] for n in e.nodes.values()]
    e.configure(MissionConfig(expected_revision=e.revision,topology=topology))
    assert e.topology_mode=='manual' and e.topology==topology
    assert positions==[n.position for n in e.nodes.values()]
    e.load_scenario('regional_jamming',e.revision)
    assert e.topology==topology and e.topology_mode=='manual'
    e.reset(); assert e.topology==topology
    e.configure(MissionConfig(expected_revision=e.revision,topology_mode='auto'))
    assert e.topology_mode=='auto' and e.adaptive.last_evaluation==0
    old=e.revision
    with pytest.raises(RevisionConflict): e.configure(MissionConfig(expected_revision=old-1,topology='grid'))


def test_pause_freezes_observations_cooldown_and_revision():
    e=engine(topology_mode='auto');run(e,2)
    e.control(MissionControl(action='pause',expected_revision=e.revision))
    before=deepcopy(e.adaptive.snapshot());revision=e.revision;at=e.seconds
    for _ in range(50):e.tick(.25)
    assert e.seconds==at and e.adaptive.snapshot()==before and e.revision==revision
    e.adaptive.transition=None;e.adaptive.last_switch=at
    e.rebuild();assert e.revision==revision
    e.reset();assert not e.adaptive.offered and e.adaptive.last_switch is None


def test_hysteresis_cooldown_ties_and_emergency_bypass(monkeypatch):
    e=engine(topology='ring');a=e.adaptive;ids=complete(e)
    e.links={edge:l for edge,l in e.physical_links.items() if edge==pair(ids[0],ids[1])}
    def evaluated(force=False):
        a.last_evaluation=e.seconds
        a.candidates={name:Candidate(name,set(),[],{}, {},{},data_pairs={('a','b')},score=.5 if name=='ring' else .7) for name in ('ring','full_mesh')}
        return a.candidates['full_mesh']
    monkeypatch.setattr(a,'evaluate',evaluated)
    e.topology_mode='auto'; a.last_evaluation=0;a.last_switch=0
    # Normal improvement requires three wins AND cooldown (no connectivity alarm).
    monkeypatch.setattr('simulation.swarm.adaptive_topology.connectivity_state',lambda *_:({('a','b')},set()))
    for at in (1,2,3):e.seconds=at;a.after_rebuild()
    assert a.wins==3 and a.transition is None
    e.seconds=15;a.after_rebuild();assert a.transition['target']=='full_mesh'
    a.cancel('test');a.last_switch=15;e.seconds=16
    monkeypatch.setattr('simulation.swarm.adaptive_topology.connectivity_state',lambda links,*_: (set(),set()) if links is e.links else ({('a','b')},{ids[0]}))
    def emergency(force=False):
        c=evaluated(force);a.candidates['ring'].data_pairs=set();return c
    monkeypatch.setattr(a,'evaluate',emergency)
    a.after_rebuild();assert a.transition is None
    e.seconds=16.2;a.after_rebuild();assert a.transition and 'Emergency' in a.reason
    a.cancel('test');a.blacklist['full_mesh']=50
    assert a.best().topology=='ring'
    a.candidates['full_mesh'].score=.5;a.blacklist.clear()
    a.candidates['ring'].data_pairs={('a','b')}
    assert a.best().topology=='ring'


def test_bounded_mesh_drain_preserves_identity_ttl_and_accounting():
    cfg=configuration(topology='full_mesh');cfg['radio']['bandwidth_mbps']=.05
    e=SwarmEngine([],cfg,isolated=True);ids=list(e.nodes)
    p=e.send(ids[0],ids[1],'fl_update',65536)
    e.scheduler.step(0,e.packet_link,e.packet_allowed)
    e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
    assert e.topology=='full_mesh' and e.adaptive.transition['phase']=='draining'
    held=e.send(ids[1],ids[2],'sensor',512);identity=held.id;created=held.created_at;expiry=held.expires_at
    direct=e.send(ids[0],'base','sensor',64,family_override='satellite')
    assert held.status=='deferred' and direct.status!='deferred'
    run(e,1.25)
    assert e.topology=='ring' and held.status!='deferred'
    assert (held.id,held.created_at,held.expires_at)==(identity,created,expiry)
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)
    assert e.adaptive.transition is None or e.adaptive.transition['committed_at']<=1.2


def test_deferred_queue_capacity_expiry_and_completed_hops():
    s=PacketScheduler(dict(ttl_s=.1,retries=0,capacity=2,queue_limit=1),random.Random(7),lambda *_:None)
    p=s.emit('a','b','sensor',64,[],0,1,defer=True)
    overflow=s.emit('a','c','sensor',64,[],0,1,defer=True)
    assert overflow.reason=='queue_overflow'
    s.step(.2,{},lambda *_:False)
    s.resume_deferred(p,['a','b'],.2,2)
    assert p.reason=='expired' and p.completed_at==.1 and not s.deferred
    assert s.generated==s.delivered+s.dropped+len(s.packets)


def test_retirement_covers_deferred_packets_without_rewriting_completed_prefix_rules():
    s=PacketScheduler(dict(ttl_s=20,retries=0,capacity=10,queue_limit=10),random.Random(7),lambda *_:None)
    held=s.emit('a','c','sensor',64,[],0,1,defer=True)
    active=s.emit('a','c','sensor',64,['a','b','c'],0,1);active.hop=1
    s.retire({'a'},0)
    assert held.reason=='node_retired' and active.status=='queued'


def test_committed_self_healing_graph_matches_prepared_candidate():
    e=engine(topology='full_mesh')
    e.configure(MissionConfig(expected_revision=e.revision,topology='self_healing_mesh'))
    prepared=e.adaptive.transition['prepared']
    assert {edge for edge in e.links if 'base' not in edge}==prepared.edges


def test_transition_validation_cannot_reinstall_a_superseded_dqn_family():
    e=engine(topology='full_mesh');source=next(iter(e.nodes))
    e.adaptive.offer(source,'base','telemetry',512,0)
    e.select_family(source,'satellite')
    e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
    for _ in range(2):e.rebuild()
    assert e.families[source]=='satellite'
    assert e.installed_rule(source,'base','telemetry')['family']=='satellite'


def test_installation_failure_rolls_back_and_blacklists_without_rewinding(monkeypatch):
    e=engine(topology='full_mesh');ids=list(e.nodes);before=e.scheduler.generated
    real=e.install_policy
    def reject(policy):
        if e.topology=='ring':return {'applied':False,'reason':'test_installation_failure'}
        return real(policy)
    monkeypatch.setattr(e,'install_policy',reject)
    e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
    assert e.topology=='full_mesh' and e.topology_mode=='manual'
    assert e.adaptive.blacklist['ring']==30
    assert e.adaptive.fallback and e.scheduler.generated==before
    assert any(event['kind']=='topology_transition_failed' for event in e.events)
    assert e.seconds==0


def test_raised_installation_failure_uses_the_same_rollback(monkeypatch):
    e=engine(topology='full_mesh');real=e.install_policy
    def reject(policy):
        if e.topology=='ring': raise RuntimeError('adapter unavailable')
        return real(policy)
    monkeypatch.setattr(e,'install_policy',reject)
    result=e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
    assert result['topology']=='full_mesh' and result['topology_selection']['fallback']=='adapter unavailable'
    assert e.adaptive.blacklist['ring']==30


def test_pending_auto_transition_cancelled_by_configuration_and_session_reset():
    e=engine(topology='full_mesh');ids=list(e.nodes)
    e.send(ids[0],ids[1],'fl_update',65536)
    e.topology_mode='auto';e.adaptive.request('ring','automatic test',True)
    p=e.send(ids[1],ids[2],'sensor')
    e.configure(MissionConfig(expected_revision=e.revision,topology='tree'))
    assert e.topology_mode=='manual' and e.adaptive.transition['target']=='tree'
    assert p.id not in e.scheduler.deferred
    old=e.session_id;e.reset()
    assert e.session_id!=old and not e.scheduler.deferred and e.topology=='tree'


def test_unrelated_configuration_retains_pending_manual_intent():
    e=engine(topology='full_mesh');ids=list(e.nodes)
    e.send(ids[0],ids[1],'fl_update',65536)
    e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
    e.configure(MissionConfig(expected_revision=e.revision,speed=.1))
    assert e.manual_topology=='ring' and e.adaptive.transition['target']=='ring'


def test_real_node_failure_emergency_restores_only_physically_achievable_pairs():
    e=engine(topology='ring',mesh_only=True);e.topology_mode='auto'
    e.seconds=16;e.adaptive.last_switch=16;e.adaptive.last_evaluation=16
    ids=list(e.nodes);e.nodes[ids[2]].available=False;e.nodes[ids[7]].available=False
    e.rebuild();before=connectivity_state(e.links,e.nodes,e.blocked|e.auto_blocked)[0]
    assert e.topology=='ring'
    e.rebuild()
    after=connectivity_state(e.links,e.nodes,e.blocked|e.auto_blocked)[0]
    assert before<after and 'Emergency' in e.adaptive.reason
    assert e.adaptive.transition['phase']=='validating'
    assert all(e.valid_route(flow['hops'],flow['family'],flow['traffic_class'] in e.control_traffic) for flow in e.flows.values())


def test_rolling_demand_counts_high_rate_offers_without_eviction():
    e=engine();ids=list(e.nodes)
    for i in range(25000):e.adaptive.offer(ids[0],ids[1],'sensor',64,0)
    assert len(e.adaptive.offered)==1
    rates,_=e.adaptive.demands();assert rates[ids[0],ids[1],'sensor']==25000*64
    e.seconds=11;assert e.adaptive.demands()[1]=='nominal_schedule_estimate'


def test_real_congestion_sustains_a_useful_change_without_forcing_all_candidates_to_win():
    e=engine(topology='star',mesh_only=True);ids=complete(e,.2)
    e.links={edge:l for edge,l in e.physical_links.items() if ids[0] in edge}
    e.topology_mode='auto';e.adaptive.last_evaluation=19;e.adaptive.last_switch=0
    for a in ids:
        for b in ids:
            if a!=b:e.adaptive.offer(a,b,'sensor',20000,20)
    for at in (20,21):e.seconds=at;e.adaptive.after_rebuild();assert e.adaptive.transition is None
    e.seconds=22;e.adaptive.after_rebuild()
    assert e.adaptive.transition and e.adaptive.transition['target']!='star'
    assert e.adaptive.wins==3 and 'utility' in e.adaptive.reason


def test_shared_destination_optimization_matches_authoritative_paths_and_ties():
    e=engine();ids=complete(e)
    for i,n in enumerate(ids):e.physical_links[pair(n,'base')]=Link(n,'base',1,-40,35,12,0,1 if i%3==0 else 5)
    adj=build_adjacency(e.physical_links,e.nodes)
    reverse=paths_to_base(adj)
    for source in ids:
        assert list(reverse[source])==shortest_path(e.physical_links,source,'base',e.nodes)
        targets={ids[(ids.index(source)+3)%len(ids)]}
        for dest,path in paths_from(adj,source,targets).items():
            if dest in targets: assert list(path)==shortest_path(e.physical_links,source,dest,e.nodes)


def test_reachability_alarm_optimization_has_identical_security_eligibility():
    e=engine();ids=complete(e)
    for n in ids:e.physical_links[pair(n,'base')]=Link(n,'base',1,-40,35,12,0,1)
    e.nodes[ids[0]].available=False;e.nodes[ids[1]].selected_route='hold'
    e.nodes[ids[2]].threat=.9;e.nodes[ids[3]].containment='quarantined'
    e.nodes[ids[4]].containment='control_only';e.nodes[ids[5]].containment='restricted'
    e.physical_links[pair(ids[6],ids[7])].loss=.5
    e.blocked.add(pair(ids[8],ids[9]))
    expected=connectivity_from_adjacencies(build_adjacency(e.physical_links,e.nodes,e.blocked),build_adjacency(e.physical_links,e.nodes,e.blocked,control=True),e.nodes)
    assert connectivity_state(e.physical_links,e.nodes,e.blocked)==expected


def test_manual_observations_are_available_when_auto_is_reenabled():
    e=engine();ids=list(e.nodes);e.send(ids[0],ids[1],'sensor',1024)
    assert e.adaptive.offered
    e.configure(MissionConfig(expected_revision=e.revision,topology_mode='auto'))
    assert e.adaptive.snapshot()['evidence']=='offered_mesh_traffic'


def test_reenabling_auto_migrates_a_legacy_incumbent_after_startup():
    e=engine(topology='manet');e.seconds=5
    e.configure(MissionConfig(expected_revision=e.revision,topology_mode='auto'))
    assert e.topology in TOPOLOGIES and e.adaptive.snapshot()['mode']=='auto'


@pytest.mark.parametrize('family',['direct','mesh'])
def test_auto_with_family_specific_jamming_and_authoritative_expiry(family):
    e=engine(topology_mode='auto');source=next(iter(e.nodes))
    e.adaptive.evaluate(True)
    before=e.adaptive.candidates['full_mesh'].factors.copy()
    e.inject_attack(MissionAttack(session_id=e.session_id,expected_revision=e.revision,attack_id='adaptive-jam',profile='spot',targets=[source],paths=[family],duration_s=2))
    e.adaptive.evaluate(True)
    if family=='direct':assert e.adaptive.candidates['full_mesh'].factors==before
    else:
        for c in e.adaptive.candidates.values():
            adj=build_adjacency(c.links,e.nodes,e.blocked|e.auto_blocked)
            assert source not in adj
    run(e,3)
    assert not e.attacks.active and e.topology_mode=='auto' and not e.security
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)


def test_reference_engines_pin_manual_even_with_auto_configuration():
    cfg=configuration(topology_mode='auto')
    request=ExperimentRequest(count=10,duration_s=10,warmup_s=0)
    for topology in TOPOLOGIES:
        e=reference_engine([],cfg,request,topology,7)
        assert e.topology_mode=='manual' and e.topology==topology
        run(e,.5)
        assert not e.adaptive.offered and e.adaptive.last_evaluation is None and e.topology==topology
    json.dumps(e.snapshot(),allow_nan=False)
