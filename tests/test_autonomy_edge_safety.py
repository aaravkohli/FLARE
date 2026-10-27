"""Edge cases discovered while auditing the complete autonomous workflow."""
from copy import deepcopy
import pytest
from schemas.autonomy import SafetyProfile
from schemas.mission import MissionCommand,MissionConfig
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.flight_safety import FlightFrame,SafetySupervisor
from simulation.swarm.terrain import Terrain
from test_autonomous_recovery import engine,run


def test_home_is_mission_initial_position_even_when_autonomy_enabled_later():
    e=engine(autonomy_mode='off');n=next(iter(e.nodes));initial=e.nodes[n].position[:]
    run(e,4);assert e.nodes[n].position!=initial
    e.configure(MissionConfig(expected_revision=e.revision,autonomy_mode='recovery_v1'))
    assert list(e.flight_safety.supervisors[n].home)==initial


def test_scenario_profile_validation_precedes_reset_and_does_not_mutate_session():
    e=engine();e.configure(MissionConfig(expected_revision=e.revision,safety_profile=SafetyProfile(geofence_m=100)))
    before=(e.session_id,e.count,e.scenario,e.revision)
    with pytest.raises(ValueError,match='geofence'):e.load_scenario('high_density',e.revision)
    assert (e.session_id,e.count,e.scenario,e.revision)==before


def test_unsafe_delivered_command_is_rejected_without_claiming_execution():
    e=engine();run(e,6);n=next(iter(e.nodes))
    e.command(MissionCommand(session_id=e.session_id,expected_revision=e.revision,command_id='outside-fence',targets=[n],action='move',parameters={'x':9000,'y':80,'z':0}))
    run(e,1);result=e.commands['outside-fence']['outcomes'][n]
    assert result['status']=='rejected_safety'
    assert 'executed_at' not in result and result.get('rejected_at') is not None
    assert 'execut' not in result['reason'].lower() or 'not executed' in result['reason'].lower()
    assert e.nodes[n].current_command!='move'


def test_unavailable_navigation_rejects_spatial_command_even_before_link_loss():
    e=engine(safety_profile=SafetyProfile(navigation_confidence=None).model_dump());run(e,6);n=next(iter(e.nodes))
    e.command(MissionCommand(command_id='unknown-navigation',targets=[n],action='move',parameters={'x':0,'y':80,'z':0}));run(e,1)
    assert e.commands['unknown-navigation']['outcomes'][n]['status']=='rejected_safety'


def test_automatic_guard_rejection_is_an_explained_decision_not_simulator_crash(monkeypatch):
    e=engine();run(e,6)
    def reject(*_):raise ValueError('no permitted replacement')
    monkeypatch.setattr(e.autonomy,'start_guard',reject)
    e.adaptive.request('ring','automatic test',True)
    assert e.adaptive.transition is None and e.adaptive.last_transition=='rejected'
    assert 'replacement' in e.adaptive.reason


def test_guard_receipts_do_not_verify_edges_removed_from_the_prepared_target():
    e=engine();run(e,6);e.adaptive.request('ring','test');g=e.autonomy.guard
    for key,(source,dest) in g['flows'].items():
        route=e.route(source,dest,'mesh',True)
        g['receipts'][key]=[dict(sent_at=e.seconds-2,received_at=e.seconds-1.9,path=route),dict(sent_at=e.seconds-1,received_at=e.seconds-.9,path=route)]
    g['candidate'].links={};g['last_send']=e.seconds
    assert e.autonomy.guard_step()!='ready'


def test_battery_caution_and_emergency_are_explicit_runtime_states():
    s=SafetySupervisor('a',[0,80,0],SafetyProfile(),Terrain())
    frame=lambda battery:FlightFrame(10,(0,80,0),(0,0,0),battery,True,None,True,(),10)
    events=s.step(frame(.19),True,'CONNECTED')
    assert s.snapshot()['battery_state']=='caution' and any(k=='battery_state_changed' for k,_ in events)
    s.step(frame(.04),True,'CONNECTED');assert s.snapshot()['battery_state']=='emergency' and s.latched


def test_enabling_after_total_loss_is_not_silent_remote_provisioning():
    e=engine(autonomy_mode='off');e.controller_connected=False;before=e.revision
    with pytest.raises(ValueError,match='before command-link loss'):
        e.configure(MissionConfig(expected_revision=e.revision,autonomy_mode='recovery_v1'))
    assert not e.autonomy.enabled and e.revision==before


def test_reenable_does_not_treat_time_spent_off_as_instant_isolation():
    e=engine();run(e,6)
    e.configure(MissionConfig(expected_revision=e.revision,autonomy_mode='off'));run(e,8)
    e.configure(MissionConfig(expected_revision=e.revision,autonomy_mode='recovery_v1'));run(e,2)
    assert not any(s.latched for s in e.flight_safety.supervisors.values())


def test_origin_revocation_still_applies_after_completed_recovery_hop():
    e=engine();a,b=list(e.nodes)[:2]
    p=e.autonomy.send([a,b,'base'],'mesh','challenge',peer='base');p.hop=1
    assert e.autonomy.allowed(p,b,'base')
    e.nodes[a].containment='quarantined';assert not e.autonomy.allowed(p,b,'base')
    e.nodes[a].containment='normal';e.autonomy.revoked.add(a);assert not e.autonomy.allowed(p,b,'base')
    e.autonomy.revoked.clear();e.flow_constraints[f'{a}:base:routing']={'excluded_nodes':[a]};assert not e.autonomy.allowed(p,b,'base')


def test_snapshot_supports_captured_ordinary_recovery_forwarding():
    e=engine();a,b=list(e.nodes)[:2]
    p=e.scheduler.emit(a,'base','telemetry',512,[a,b,'base'],e.seconds,e.network_revision,'ordinary-recovery','mesh')
    e.autonomy.bind_forwarding(p,dict(route=[a,b,'base'],expires_at=e.seconds+3))
    # Captured ordinary packets are not registered control messages. Their
    # forwarding must remain inspectable without requiring control-only fields.
    snapshot=e.snapshot()
    assert any(edge['a']==a and edge['b']==b for edge in snapshot['autonomy']['overlay_edges'])
    assert snapshot['autonomy']['overlay_edges'][0]['kind']=='recovery_control'


def test_other_transport_response_cannot_shift_satellite_channel_draw():
    first=engine();second=engine();n=next(iter(first.nodes))
    second.autonomy.send(['base',n],'mesh','response',peer='base',sent_at=1)
    a=first.autonomy.send(['base',n],'satellite','response',peer='base',sent_at=1)
    b=second.autonomy.send(['base',n],'satellite','response',peer='base',sent_at=1)
    assert a.channel_key==b.channel_key
    assert first.scheduler.draw(a,('base',n),'loss')==second.scheduler.draw(b,('base',n),'loss')


def test_local_switching_knowledge_requires_a_delivered_policy_message():
    e=engine();run(e,6);n=next(iter(e.nodes));c=e.autonomy.controllers[n]
    # Coordinator state is globally visible to the UI, never an onboard input.
    e.autonomy.guard={'target':'ring'};e.autonomy.last_send=e.seconds
    e.tick(.1);assert c.state!='SWITCHING'
    before=deepcopy(c.last_ground)
    assert c.receive(dict(id='delivered-policy',sender='base',kind='response',peer='base',family='mesh',guard='ring',expires_at=e.seconds+3),e.seconds)
    assert c.last_ground==before
    e.autonomy.last_send=e.seconds;e.tick(.1);assert c.state=='SWITCHING'


def test_actual_landing_descent_is_bounded_at_each_movement_step():
    e=engine();n=next(iter(e.nodes));e.nodes[n].battery=.08
    e.autonomy.interfaces[n]=dict(direct=False,satellite=False,mesh=False)
    e.playing=True;descent=[]
    for _ in range(2400):
        d=e.nodes[n];before=d.position[1];mode=d.mode;e.tick(.05)
        if mode=='landing' and d.mode in ('landing','landed'):descent.append((before-d.position[1])/.05)
        assert abs(d.position[0])<=2500 and abs(d.position[2])<=2500
        if d.mode=='landed':break
    assert descent and min(descent)>=-1e-6 and max(descent)<=1.00001
    assert e.flight_safety.supervisors[n].outcome=='landed'
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)


@pytest.mark.parametrize('speed',[.1,.25,.5,1.,2.,4.])
def test_real_loss_and_stable_recovery_follow_simulation_time_at_every_speed(speed):
    e=engine();e.speed=speed;e.playing=True;n=next(iter(e.nodes))
    def advance_seconds(duration):
        until=e.seconds+duration
        while e.seconds<until-1e-8:e.tick(.05)
    advance_seconds(6)
    assert e.autonomy.controllers[n].stable
    e.autonomy.interfaces[n]=dict(direct=False,satellite=False,mesh=False)
    advance_seconds(4)
    assert e.autonomy.controllers[n].health=='COMPLETELY_ISOLATED'
    assert e.flight_safety.supervisors[n].latched
    e.playing=False;before=(e.seconds,e.autonomy.controllers[n].attempts)
    for _ in range(20):e.tick(.25)
    assert (e.seconds,e.autonomy.controllers[n].attempts)==before
    e.autonomy.interfaces.clear();e.playing=True;advance_seconds(7)
    assert e.autonomy.controllers[n].stable and e.autonomy.controllers[n].state=='RECONNECTED'
    assert e.nodes[n].mode=='safety_hold' and e.flight_safety.supervisors[n].latched


def test_manual_configuration_rejects_a_real_last_path_loss_before_mutation():
    e=engine(autonomy_mode='off');e.cfg['radio']['range_m']=160
    for i,d in enumerate(e.nodes.values()):
        d.position=[-250+80*i,80,180];d.target=d.position[:];d.range_m=160;d.mode='hold'
    e.rebuild();e.configure(MissionConfig(expected_revision=e.revision,autonomy_mode='recovery_v1'));run(e,6)
    node=list(e.nodes)[4]
    assert e.route(node,'base','mesh',True) and e.autonomy.controllers[node].recent_ground(e.seconds)
    before=(e.revision,e.topology,e.topology_mode,e.manual_topology,[d.position[:] for d in e.nodes.values()])
    with pytest.raises(ValueError,match='Topology rejected'):
        e.configure(MissionConfig(expected_revision=e.revision,topology='tree'))
    assert before==(e.revision,e.topology,e.topology_mode,e.manual_topology,[d.position[:] for d in e.nodes.values()])
