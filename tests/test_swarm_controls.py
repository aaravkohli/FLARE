"""Packet-level condition effects and acknowledged control; no field claims."""
from copy import deepcopy
import pytest
from fleet.registry import list_drones
from schemas.mission import MissionAttack, ClearMissionAttacks, MissionCommand, MissionConfig
from simulation.swarm.attacks import PROFILES
from simulation.swarm.engine import SwarmEngine, RevisionConflict
from simulation.swarm.network import Link
from simulation.swarm.topology import pair

@pytest.fixture
def mission(monkeypatch,tmp_path):
    monkeypatch.setenv('FLARE_BYZANTINE_STATE_PATH',str(tmp_path/'byzantine.json'))
    e=SwarmEngine(list_drones());e.configure(MissionConfig(expected_revision=e.revision,count=10))
    return e

def inject(e,profile,group='ew',targets=None,paths=None,duration=15):
    return e.inject_attack(MissionAttack(session_id=e.session_id,expected_revision=e.revision,attack_id=f'{group}-{profile}',
        group=group,profile=profile,targets=targets or [next(iter(e.nodes))],paths=['mesh'] if paths is None else paths,duration_s=duration))

def observe(e,seconds=2):
    for _ in range(round(seconds/.05)):
        e.seconds+=.05;e.attacks.step(.05);e.attacks.generate(.05)
        e.scheduler.step(e.seconds,e.packet_link,e.packet_allowed,e.attacks.relay_drop)
        e.expire_commands()

def lossless(e,hops=None):
    edges=list(zip(hops,hops[1:])) if hops else [(n,'base') for n in e.nodes]
    e.links={pair(a,b):Link(a,b,20,-50,30,12,0,1) for a,b in edges}
    e.radio_links=deepcopy(e.links);e.flows.clear();e.auto_blocked.clear();e._clear_routes()

@pytest.mark.parametrize('group,profile',[(g,p) for g,profiles in PROFILES.items() for p in profiles])
def test_condition_contract_lifecycle_pause_reset(mission,group,profile):
    e=mission;identity=next(iter(e.nodes));before=e.seconds
    record=inject(e,profile,group,paths=[] if group!='ew' else ['mesh'],duration=1)
    assert record['status'] in ('active','awaiting_participating_round','cleared')
    e.tick(5);assert e.seconds==before
    assert not any('profile' in row['security_evidence'] for row in e.metrics().values())
    observe(e,1.1);assert not e.attacks.active
    session=e.session_id;e.reset();assert session!=e.session_id and not e.attacks.active
    with pytest.raises(RevisionConflict):e.inject_attack(MissionAttack(session_id=session,expected_revision=e.revision,attack_id='stale',profile='spot',targets=[identity]))

@pytest.mark.parametrize('profile',['spot','sweep','barrage','smart','reactive','adaptive'])
def test_rf_conditions_affect_channels_and_satellite(mission,profile):
    e=mission;n=next(iter(e.nodes));baseline=e.satellite_link(n)
    inject(e,profile,paths=['satellite']);e.families[n]='satellite';e.attacks.step(.05)
    impaired=e.satellite_link(n)
    if profile=='sweep':e.seconds=4;e.attacks.step(.05);impaired=e.satellite_link(n)
    if profile=='adaptive':
        assert e.attacks.q and e.attacks.active['ew-adaptive']['outcomes'][n]['jammed_family'] in ('direct','satellite','mesh')
    else:assert impaired.sinr<baseline.sinr and impaired.loss>baseline.loss
    assert not e.security # Conditions do not manufacture detector findings.

def test_fhss_mitigation_coexists_and_clearing_preserves_containment(mission):
    e=mission;n=next(iter(e.nodes));inject(e,'barrage');e.attacks.step(.05);bad=e.satellite_link(n)
    inject(e,'fhss',paths=['satellite']);good=e.satellite_link(n)
    assert good.sinr>bad.sinr and len(e.attacks.active)==2
    e.contain(n,'quarantined','detector')
    e.clear_attacks(ClearMissionAttacks(session_id=e.session_id,expected_revision=e.revision))
    assert not e.attacks.active and e.nodes[n].containment=='quarantined'

@pytest.mark.parametrize('profile,group',[('dos','ew'),('control_flood','traffic'),('replay','traffic'),('spoofing','ew'),('telemetry_falsification','traffic')])
def test_packet_outcomes_generate_independent_observations(mission,profile,group):
    e=mission;n=next(iter(e.nodes));inject(e,profile,group,paths=[] if group=='traffic' else ['mesh']);lossless(e)
    if profile in ('spoofing','telemetry_falsification'):
        for _ in range(10):e.send(n,'base','telemetry')
    observe(e,3)
    evidence=e.metrics()[n]['security_evidence']
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)
    if profile=='dos':
        assert e.scheduler.dropped>0 and e.scheduler.generated>=1000
        assert any(r[2]=='data' for r in e.scheduler.observations)
    if profile=='control_flood':assert evidence['control_messages_per_s']>50
    if profile=='replay':assert evidence['duplicate_sequence_ratio']>.5
    if profile=='spoofing':assert any(r[2]=='source_identity' and r[3]!=e.records[n]['mac'] for r in e.scheduler.observations)
    if profile=='telemetry_falsification':assert evidence['reported_forwarded_packets']>evidence['controller_rx_packets']

def test_selective_relay_drops_and_gps_reporting_do_not_change_physics(mission):
    e=mission;ids=list(e.nodes)[:3];inject(e,'selective_forwarding','traffic',[ids[1]],paths=[]);lossless(e,[*ids,'base'])
    packets=[e.send(ids[0],'base','telemetry') for _ in range(40)];observe(e,1)
    assert any(p.reason=='relay_discard' for p in packets)
    row=e.metrics()[ids[1]]['security_evidence'];assert row['controller_dropped_packets']>0
    before=e.nodes[ids[0]].position[:];inject(e,'gps_spoofing',targets=[ids[0]])
    assert e.nodes[ids[0]].position==before and e.attacks.position(ids[0],before)!=before

def test_unreceived_identity_is_unavailable_instead_of_invented(mission):
    e=mission;n=next(iter(e.nodes))
    evidence=e.metrics()[n]['security_evidence']
    assert evidence['observed_source_mac'] is None
    assert evidence['expected_source_mac']==e.records[n]['mac']
    lossless(e);e.send(n,'base','telemetry');observe(e,.2)
    assert e.metrics()[n]['security_evidence']['observed_source_mac']==e.records[n]['mac']

def test_actual_fl_dispatch_arms_only_participating_target(mission):
    from simulation.byzantine_state import load_attack_state
    e=mission;ids=list(e.nodes)[:2];inject(e,'poisoned','fl',ids,paths=[])
    assert not any(v=='poisoned' for v in load_attack_state().values())
    e.fl_dispatch(3,[ids[1]])
    assert load_attack_state()[ids[1]]=='poisoned' and load_attack_state().get(ids[0],'normal')=='normal'
    assert e.attacks.active['fl-poisoned']['outcomes'][ids[1]]['round']==3
    e.reset();assert not any(v=='poisoned' for v in load_attack_state().values())

@pytest.mark.parametrize('action,params',[('hold',{}),('resume',{}),('return',{}),('emergency',{}),('altitude',{'altitude':120}),('move',{'x':30,'y':100,'z':50}),('waypoints',{'points':[[0,100,0],[10,100,10]],'loop':True}),('formation',{'formation':'v'}),('route',{'family':'hold'}),('reroute',{}),('role',{'role':'gateway'}),('disconnect',{}),('reconnect',{}),('isolate',{}),('restore',{})])
def test_every_command_has_authoritative_effect(mission,action,params):
    e=mission;n=next(iter(e.nodes));lossless(e);targets=list(e.nodes) if action=='formation' else [n]
    request=MissionCommand(session_id=e.session_id,expected_revision=e.revision,command_id=action,targets=targets,action=action,parameters=params)
    result=e.command(request)
    management=action in ('disconnect','reconnect','isolate','restore')
    assert result['outcomes'][n]['status']==('management_applied' if management else 'queued')
    if not management:
        assert result['outcomes'][n]['reason']=='Waiting for mission to play'
        e.tick(2);assert e.commands[action]['outcomes'][n]['status']=='queued'
        e.playing=True;e.dispatch_commands();observe(e,.8)
        assert e.commands[action]['outcomes'][n]['status']=='acknowledged'
        o=e.commands[action]['outcomes'][n];assert o['accepted_at']<=o['delivered_at']<=o['acknowledged_at']
    d=e.nodes[n]
    if action in ('hold','resume','return','emergency','move','altitude','waypoints'):assert d.mode=={'hold':'hold','resume':'patrol','return':'return','emergency':'return','move':'move','altitude':'move','waypoints':'waypoints'}[action]
    if action=='move':assert d.target==[30,100,50]
    if action=='altitude':assert d.target[1]==120
    if action=='formation':assert all(d.formation_slot is not None for d in e.nodes.values())
    if action=='route':assert e.families[n]=='hold' and d.mode=='patrol'
    if action=='reroute':assert 'changed' in e.commands[action]['outcomes'][n]['result']
    if action=='role':assert d.role=='gateway'
    if action in ('disconnect','reconnect'):assert d.available==(action=='reconnect')
    if action in ('isolate','restore'):assert d.containment==('quarantined' if action=='isolate' else 'normal')

def test_partial_formation_and_idempotency_never_execute_unreachable_targets(mission):
    e=mission;ids=list(e.nodes);lossless(e);e.nodes[ids[-1]].available=False;e.playing=True
    request=MissionCommand(session_id=e.session_id,expected_revision=e.revision,command_id='partial',targets=ids,action='formation',parameters={'formation':'circle'})
    e.command(request);observe(e,.8)
    assert e.commands['partial']['outcomes'][ids[0]]['status']=='acknowledged'
    assert e.commands['partial']['outcomes'][ids[-1]]['status']=='delivery_failed' and e.nodes[ids[-1]].formation_slot is None
    generated=e.scheduler.generated;e.revision+=1;e.command(request);assert e.scheduler.generated==generated
    with pytest.raises(ValueError):e.command(request.model_copy(update={'parameters':{'formation':'v'}}))

def test_role_elects_gateway_and_stale_attack_is_rejected(mission):
    e=mission;e.configure(MissionConfig(expected_revision=e.revision,topology='star'));n=list(e.nodes)[1]
    e.execute_command(n,{'action':'role','parameters':{'role':'gateway'},'command_id':'role','outcomes':{n:{}}})
    assert e.heads==[n]
    request=MissionAttack(session_id=e.session_id,expected_revision=e.revision-1,attack_id='conflict',profile='spot',targets=[n])
    with pytest.raises(RevisionConflict):e.inject_attack(request)
