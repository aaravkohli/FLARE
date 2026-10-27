"""Onboard information boundaries, actual receipt verification and flight outcomes."""
from copy import deepcopy
from pathlib import Path
import inspect
import json
import pytest
import yaml
from schemas.mission import MissionConfig,MissionCommand,MissionControl,ExperimentRequest
from schemas.autonomy import SafetyProfile
from simulation.swarm.engine import SwarmEngine,RevisionConflict
from simulation.swarm.autonomy import LocalNetworkController,LocalFrame
from simulation.swarm.experiments import reference_engine


def engine(**changes):
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text());cfg.update(default_count=10,topology_mode='manual',topology='full_mesh',autonomy_mode='recovery_v1');cfg.update(changes)
    cfg['radio'].update(range_m=1000,reference_rssi_dbm=-20,noise_dbm=-110)
    return SwarmEngine([],cfg,isolated=True)


def run(e,seconds):
    e.playing=True
    for _ in range(round(seconds/.05)):e.tick(.05)


def test_opt_in_and_existing_experiment_isolation():
    off=SwarmEngine([],isolated=True);run(off,.5)
    assert not off.autonomy.enabled and not off.autonomy.controllers and not off.autonomy.bindings
    e=engine();run(e,1);assert e.autonomy.controllers
    ref=reference_engine([],e.cfg,ExperimentRequest(),'ring',7)
    assert not ref.autonomy.enabled and 'safety_profile' not in ref.cfg
    e.reset();assert e.autonomy.enabled and not e.autonomy.counters and all(c.disconnected_since is None for c in e.autonomy.controllers.values())


def test_local_controller_has_no_engine_graph_or_injection_inputs():
    c=LocalNetworkController('a',('a','b'),0)
    assert not any(k in vars(c) for k in ('engine','graph','attacks','nodes','physical_links'))
    source=inspect.getsource(LocalNetworkController)
    assert 'self.engine' not in source and 'physical_links' not in source
    c.step(LocalFrame(3.1,True,(('mesh',True),),0,False,False))
    assert c.health=='COMPLETELY_ISOLATED' and c.disconnected_since==3.1
    c.step(LocalFrame(13.2,True,(),0,False,False));assert c.state=='FAILSAFE'
    assert c.diagnosis['label']=='Undetermined'


def test_real_scheduler_receipts_and_controller_loss_without_false_recovery():
    e=engine();run(e,6);n=next(iter(e.nodes));c=e.autonomy.controllers[n]
    assert c.stable and c.last_ground and e.autonomy.counters['delivered']>0
    e.controller_connected=False;run(e,4)
    assert c.health=='PARTIALLY_DISCONNECTED' and e.flight_safety.supervisors[n].latched
    e.controller_connected=True;run(e,7)
    assert c.state=='RECONNECTED' and e.nodes[n].mode=='safety_hold'
    e.command(MissionCommand(session_id=e.session_id,expected_revision=e.revision,command_id='resume',targets=[n],action='resume'))
    run(e,1);assert not e.flight_safety.supervisors[n].latched and e.nodes[n].mode=='patrol'


def test_physical_faults_block_ordinary_and_recovery_traffic_and_restore():
    e=engine();run(e,6);n=next(iter(e.nodes));e.autonomy.interfaces[n]=dict(direct=False,satellite=False,mesh=False)
    p=e.send(n,'base','telemetry');run(e,4)
    assert not e.autonomy.controllers[n].recent_ground(e.seconds)
    assert e.autonomy.controllers[n].health=='COMPLETELY_ISOLATED'
    assert e.flight_safety.supervisors[n].latched
    assert p.completed_at is not None and p.status=='dropped'
    e.autonomy.interfaces.clear();run(e,7);assert e.autonomy.controllers[n].stable


def test_verified_transition_keeps_incumbent_until_two_delivered_round_trips():
    e=engine();run(e,6)
    e.adaptive.request('ring','test');assert e.adaptive.transition['phase']=='verifying'
    original=e.topology;run(e,.7);assert e.topology==original
    run(e,3);assert e.topology=='ring' and e.autonomy.counters['guard_verified']==1
    assert not e.autonomy.guard and all(e.valid_route(f['hops'],f['family'],True) for f in e.flows.values() if f['traffic_class']=='heartbeat')


def test_lost_transition_receipts_do_not_retire_incumbent_and_rollback():
    e=engine();run(e,6);e.adaptive.request('ring','test');e.controller_connected=False
    run(e,3.5);assert e.topology=='full_mesh' and e.autonomy.counters['prevented_transitions']>=1
    assert e.adaptive.fallback and not e.autonomy.guard


def test_registered_overlay_cannot_authorize_other_packets_or_ignore_revocation():
    e=engine();run(e,.1);p=next(p for p in e.scheduler.packets.values() if e.autonomy.owns(p))
    q=deepcopy(p);q.id='fake';assert not e.autonomy.allowed(q,q.route[0],q.route[1])
    e.autonomy.revoked.add(p.route[0]);assert not e.autonomy.allowed(p,p.route[0],p.route[1])
    assert all(p.service_kind is None for p in e.scheduler.packets.values() if p.category=='sensor')


def test_stale_advertisements_loops_duplicates_and_scope():
    c=LocalNetworkController('a',('a','b','c'),0)
    message=dict(id='1',kind='response',sender='b',recipient='a',peer='b',expires_at=3,family='mesh',sent_at=0,advertisement=dict(route=['b','c','base'],sequence=1))
    assert c.receive(message,.1);assert not c.receive(message,.2)
    assert c.advertised_route(.5)==['a','b','c','base'];assert not c.advertised_route(3.1)
    message.update(id='2',advertisement=dict(route=['b','a','base'],sequence=2))
    c.receive(message,.5);assert c.ground_vectors['b']['sequence']==1
    assert len(c.peer_targets(1))<=4


def test_safety_profile_validation_and_revision_boundaries():
    with pytest.raises(ValueError):SafetyProfile(critical_battery=.3)
    with pytest.raises(ValueError):SafetyProfile(landing_areas=[dict(id='outside',x=3000,z=0)])
    e=engine()
    with pytest.raises(RevisionConflict):e.configure(MissionConfig(expected_revision=e.revision-1,autonomy_mode='off'))
    e.nodes[next(iter(e.nodes))].position[0]=150
    with pytest.raises(ValueError):e.configure(MissionConfig(expected_revision=e.revision,safety_profile=SafetyProfile(geofence_m=100)))
    assert e.autonomy.enabled


def test_low_battery_isolation_lands_and_never_automatically_relaunches():
    e=engine();n=next(iter(e.nodes));e.nodes[n].battery=.08;e.autonomy.interfaces[n]=dict(direct=False,satellite=False,mesh=False)
    run(e,120);s=e.flight_safety.supervisors[n]
    assert s.outcome=='landed' and e.nodes[n].mode=='landed' and s.counters['unsafe_contacts']==0
    e.autonomy.interfaces.clear();run(e,7)
    e.command(MissionCommand(command_id='no-relaunch',targets=[n],action='resume'));run(e,.5)
    assert e.nodes[n].mode=='landed' and s.latched


def test_missing_safety_inputs_never_manufacture_safe_landing_and_disable_keeps_latch():
    e=engine(safety_profile=SafetyProfile(navigation_confidence=None,obstacle_sensor_available=None).model_dump());n=next(iter(e.nodes));e.nodes[n].battery=.08
    run(e,2);s=e.flight_safety.supervisors[n];assert s.action=='safety_hold' and s.unresolved
    e.configure(MissionConfig(expected_revision=e.revision,autonomy_mode='off'));run(e,1)
    assert s.latched and e.nodes[n].mode=='safety_hold' and s.outcome is None
    e.nodes[n].battery=0;run(e,8)
    assert s.counters['battery_exhaustions']==1 and s.outcome!='landed'


def test_profile_changes_wait_for_packet_delivery():
    e=engine();run(e,6);n=next(iter(e.nodes));e.controller_connected=False
    # Profile transport remains mesh but no route cannot deliver the change.
    e.autonomy.interfaces[n]=dict(mesh=False)
    new=SafetyProfile(navigation_confidence=None)
    e.configure(MissionConfig(expected_revision=e.revision,safety_profile=new))
    assert e.flight_safety.supervisors[n].profile.navigation_confidence==1
    run(e,3);assert e.flight_safety.supervisors[n].profile.navigation_confidence==1


@pytest.mark.parametrize('speed',[.1,.25,.5,1.,2.,4.])
def test_simulation_time_and_pause_freeze_recovery(speed):
    e=engine();e.speed=speed;e.playing=True
    for _ in range(round(1/(.05*speed))):e.tick(.05)
    before=e.seconds;e.playing=False
    for _ in range(20):e.tick(.25)
    assert e.seconds==before and e.autonomy.last_monitor<=before


def test_manual_guard_rejection_preserves_management_revision_and_active_topology():
    e=engine();run(e,6);before=(e.revision,e.topology,e.manual_topology,e.topology_mode)
    from simulation.swarm.adaptive_topology import Candidate
    bad=Candidate('ring',set(),[],{}, {},{})
    with pytest.raises(ValueError):e.autonomy.check_candidate(bad)
    assert before==(e.revision,e.topology,e.manual_topology,e.topology_mode)


def test_asymmetric_response_loss_does_not_count_as_roundtrip():
    e=engine();n=next(iter(e.nodes));e.autonomy.send([n,'base'],'mesh','challenge',peer='base')
    # Deliver request, then suppress only replies. Transport is an explicit
    # asymmetric fixture; no detector class is manufactured.
    original=e.autonomy.allowed
    e.autonomy.allowed=lambda p,a,b:False if p.source=='base' else original(p,a,b)
    run(e,4);assert not e.autonomy.controllers[n].recent_ground(e.seconds)


def test_diagnostic_confidence_never_identifies_jamming_from_loss_alone():
    c=LocalNetworkController('a',('a','b'),0)
    for i in range(10):c.samples.append(dict(at=1,success=False,sinr=None,rssi=None))
    c.step(LocalFrame(1,True,(('mesh',None),),0,False,False));assert c.diagnosis['label']=='Undetermined'
    for row in c.samples:row.update(sinr=0,rssi=-60)
    c.step(LocalFrame(1.1,True,(),0,False,False));assert c.diagnosis['label']=='Undetermined'
    assert {h['cause'] for h in c.diagnosis['hypotheses']}=={'Temporary interference','Suspected intentional jamming'}
    c.step(LocalFrame(1.2,True,(('mesh',False),),0,False,False));assert c.diagnosis['label']=='Interface self-test failure'


@pytest.mark.parametrize('count',range(10,101,10))
@pytest.mark.parametrize('seed',[7,42,99])
def test_all_ten_candidates_with_autonomy_are_deterministic_and_private(count,seed):
    e=engine(default_count=count,seed=seed)
    e.adaptive.evaluate(True);first=[c.diagnostic() for c in e.adaptive.candidates.values()]
    before=deepcopy((e.nodes,e.flows,e.scheduler.packets,e.rng.getstate(),e.autonomy.controllers))
    e.adaptive.evaluate(True)
    assert len(first)==10 and first==[c.diagnostic() for c in e.adaptive.candidates.values()]
    assert before==(e.nodes,e.flows,e.scheduler.packets,e.rng.getstate(),e.autonomy.controllers)


def test_unrelated_control_offers_do_not_shift_challenge_channel_keys():
    a=engine();b=engine();n,peer=list(a.nodes)[:2]
    b.autonomy.send([peer,'base'],'satellite','challenge',peer='base')
    first=a.autonomy.send([n,'base'],'satellite','challenge',peer='base')
    second=b.autonomy.send([n,'base'],'satellite','challenge',peer='base')
    assert first.channel_key==second.channel_key
    assert a.scheduler.draw(first,(n,'base'),'loss')==b.scheduler.draw(second,(n,'base'),'loss')


def test_supervisor_prioritizes_obstacles_geofence_and_unavailable_sites():
    from simulation.swarm.flight_safety import SafetySupervisor,FlightFrame
    from simulation.swarm.terrain import Terrain
    p=SafetyProfile(geofence_m=100,landing_areas=[dict(id='clear',x=50,z=0)],obstacles=[dict(id='block',x=0,z=0,radius_m=10,height_m=200)])
    s=SafetySupervisor('a',[0,80,0],p,Terrain());assert not s.home_valid
    f=FlightFrame(20,(30,80,0),(0,0,0),.5,True,3,False,(),20)
    assert s.choose_site(f)['id']=='clear'
    assert not s.segment_safe((30,80,0),(0,80,0)) and not s.segment_safe((30,80,0),(110,80,0))
    s.profile.landing_areas=[];s.step(f,True,'FAILSAFE');assert s.action=='safety_hold' and s.unresolved


def test_failsafe_freshness_and_delivered_resume_constraints_are_independent():
    from simulation.swarm.flight_safety import SafetySupervisor,FlightFrame
    from simulation.swarm.terrain import Terrain
    s=SafetySupervisor('a',[0,80,0],SafetyProfile(fixture_refresh=False),Terrain())
    f=FlightFrame(20,(0,80,0),(0,0,0),.5,True,3,True,(),0)
    s.step(f,True,'RECONNECTED');assert s.action=='safety_hold' and s.unresolved
    ok,reason=s.command_allowed('resume',f);assert not ok and reason
    s.profile.navigation_confidence=1;s.profile.obstacle_sensor_available=True
    fresh=FlightFrame(20,(0,80,0),(0,0,0),.5,True,None,True,(),20)
    ok,_=s.command_allowed('resume',fresh);assert ok and not s.latched


def test_dual_family_interference_retains_delivered_mesh_contact():
    e=engine();run(e,6);n=next(iter(e.nodes));c=e.autonomy.controllers[n]
    assert c.provisioned_mesh and c.last_ground.get('mesh')
    e.autonomy.interfaces[n]=dict(direct=False,satellite=False)
    run(e,9)
    assert c.recent_ground(e.seconds).get('mesh') and c.disconnected_since is None
    assert not e.flight_safety.supervisors[n].latched
