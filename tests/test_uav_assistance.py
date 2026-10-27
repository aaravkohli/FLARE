"""Registered evidence, policy bounds and actual packet-delivered verification."""
from copy import deepcopy
from pathlib import Path
import pytest
import yaml
from schemas.mission import MissionConfig,ExperimentRequest,TOPOLOGIES
from simulation.swarm.engine import SwarmEngine,RevisionConflict
from simulation.swarm.experiments import reference_engine
from simulation.swarm.topology import pair


def engine(**patch):
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg.update(default_count=10,topology='full_mesh',topology_mode='manual',affected_uav_assist=True,autonomy_mode='off',logical_channel_keys=True)
    cfg.update(patch);cfg['radio'].update(range_m=1000,reference_rssi_dbm=-20,noise_dbm=-110)
    e=SwarmEngine([],cfg,isolated=True)
    for n in e.nodes.values():n.mode='hold'
    return e


def run(e,seconds):
    e.playing=True
    for _ in range(round(seconds/.05)):e.tick(.05)


def evidence(e,at,delivered=8,dropped=0,latency=20,source=None,dest=None):
    a=e.assistance;s,d=source or list(e.nodes)[0],dest or list(e.nodes)[1]
    for i in range(delivered+dropped):
        identity=f'fixture:{at}:{i}:{s}'
        a.register(identity,s,d,'sensor',512,at-latency/1000)
        a.result('packet_result',dict(id=identity,source=s,destination=d,created_at=at-latency/1000,completed_at=at,status='delivered' if i<delivered else 'dropped'))
    return s,d


def test_evidence_thresholds_windows_recovery_and_pause():
    e=engine();a=e.assistance;s,_=evidence(e,1,delivered=6,dropped=2)
    for t in (1,2):a.update(t);assert not a.nodes[s]['degraded']
    a.update(3);assert a.nodes[s]['degraded'] and a.nodes[s]['pdr']==.75
    # An empty/stale window cannot imply recovery.
    a.update(12);assert a.nodes[s]['degraded'] and a.nodes[s]['pdr'] is None
    for t in range(13,17):evidence(e,t);a.update(t);assert a.nodes[s]['degraded']
    evidence(e,17);a.update(17);assert not a.nodes[s]['degraded']
    before=deepcopy(a.snapshot());e.playing=False
    for _ in range(20):e.tick(.25)
    assert a.snapshot()==before


def test_missing_receipts_uses_real_first_offer_and_three_confirmation_evaluations():
    e=engine();a=e.assistance;a.update(0);s,d=list(e.nodes)[:2]
    for i in range(3):a.register(str(i),s,d,'sensor',512,20)
    a.update(21);assert not a.nodes[s]['missing_receipts']
    for t in (23,24):a.update(t);assert not a.nodes[s]['degraded']
    a.update(25);assert a.nodes[s]['degraded'] and a.nodes[s]['status']=='Missing receipts'
    assert a.nodes[s]['pdr'] is None and a.nodes[s]['receipt_age_s']==5


def test_category_spoofing_and_direct_traffic_cannot_nominate():
    e=engine();s,d=list(e.nodes)[:2]
    for _ in range(10):
        e.send(s,d,'sensor',channel_key='unregistered')
        e.send(s,'base','telemetry',family_override='direct',_mission_offer=True)
    assert not e.assistance.pending and not e.assistance.buckets
    trusted=e.send(s,d,'sensor',_mission_offer=True)
    assert trusted.id in e.assistance.pending
    run(e,.2);assert e.assistance.nodes[s]['last_receipt'] is not None
    assert e.adaptive.offered # Unregistered traffic still contributes demand.


@pytest.mark.parametrize('restriction',['quarantined','hold','unavailable','excluded'])
def test_prohibited_traffic_is_not_recovery_evidence(restriction):
    e=engine();s,d=list(e.nodes)[:2]
    if restriction=='quarantined':e.contain(s,'quarantined','fixture')
    elif restriction=='hold':e.select_family(s,'hold')
    elif restriction=='unavailable':e.nodes[s].available=False
    else:e.flow_constraints[f'{s}:{d}:sensor']={'allowed_traffic_classes':['heartbeat']}
    for _ in range(10):e.send(s,d,'sensor',_mission_offer=True)
    for t in range(8):e.assistance.update(t)
    assert not e.assistance.nodes[s]['degraded']
    assert not e.assistance.pending


def test_legacy_reset_scenario_manual_and_experiment_contract():
    e=engine();s,d=evidence(e,1,delivered=0,dropped=8)
    for t in (1,2,3):e.assistance.update(t)
    assert 'manual topology pinned' in e.assistance.snapshot()['reason']
    old=e.revision
    with pytest.raises(RevisionConflict):e.configure(MissionConfig(expected_revision=old-1,affected_uav_assist=False))
    assert e.assistance.enabled
    e.load_scenario('mobility',e.revision);assert e.assistance.enabled and not e.assistance.pending
    e.reset();assert e.assistance.enabled and not any(n['degraded'] for n in e.assistance.nodes.values())
    ref=reference_engine([],e.cfg,ExperimentRequest(),'ring',7)
    assert not ref.assistance.enabled and ref.topology_mode=='manual'
    e.configure(MissionConfig(expected_revision=e.revision,affected_uav_assist=False));assert not e.assistance.enabled


@pytest.mark.parametrize('seed',[7,42,99])
@pytest.mark.parametrize('count',range(10,101,10))
def test_ten_candidate_assistance_scoring_is_deterministic_and_private(count,seed):
    e=engine(default_count=count,seed=seed);evidence(e,1,delivered=0,dropped=8)
    for t in (1,2,3):e.assistance.update(t)
    before=deepcopy((e.nodes,e.topology_state,e.links,e.flows,e.forwarding,e.rng.getstate(),e.scheduler.generated))
    e.adaptive.evaluate(True);first=[(c.diagnostic(),c.services) for c in e.adaptive.candidates.values()]
    e.adaptive.evaluate(True);assert first==[(c.diagnostic(),c.services) for c in e.adaptive.candidates.values()]
    assert len(first)==10 and before==(e.nodes,e.topology_state,e.links,e.flows,e.forwarding,e.rng.getstate(),e.scheduler.generated)
    assert all('base' not in route[1:-1] for c in e.adaptive.candidates.values() for m in c.services.values() for route in [m['route']])


def stage(e,target='ring'):
    a=e.assistance;s,d=evidence(e,1,delivered=0,dropped=8)
    for t in (1,2,3):a.update(t)
    e.seconds=3
    e.topology_mode='auto';a.proposal={'target':target,'node':s,'affected':[s,d],'metrics':deepcopy(a.nodes[s]),'gain':.2}
    # Unit fixture directly exercises verification/coordinator, not policy selection.
    e.adaptive.request(target,'Affected UAV fixture · estimated improvement',True)
    return s,d


def test_verification_runs_without_flight_autonomy_keeps_incumbent_and_spacing():
    e=engine();e.generate_traffic=lambda:None;s,d=stage(e)
    assert not e.autonomy.enabled and not e.autonomy.controllers
    assert e.adaptive.transition['phase']=='verifying'
    run(e,.8);assert e.topology=='full_mesh'
    g=e.autonomy.guard;assert g and all(len(rows)==1 for rows in g['receipts'].values())
    run(e,.5)
    # Two genuine scheduled round trips, not synthetic acknowledgments.
    assert e.autonomy.counters['guard_verified']==1
    assert not any(s.latched for s in e.flight_safety.supervisors.values())
    run(e,1);assert e.topology=='full_mesh' and not e.assistance.counters['switches']
    assert 'improvement no longer' in e.adaptive.fallback # fixture forces a route with no service gain


def test_uncertain_failed_probes_timeout_backoff_and_no_false_quality():
    e=engine();e.generate_traffic=lambda:None;stage(e);e.controller_connected=False
    run(e,3.5)
    assert e.topology=='full_mesh' and e.adaptive.blacklist['ring']>e.seconds
    assert e.assistance.history[-1]['status']=='Transition failed'
    assert not e.autonomy.guard and not e.assistance.counters['switches']


def test_config_cancellation_and_revocation_invalidate_packet_specific_probe_permission():
    e=engine();e.cfg['radio']['propagation_ms']=100;e.rebuild(adapt=False);e.generate_traffic=lambda:None;s,d=stage(e);run(e,.05)
    p=next(p for p in e.scheduler.packets.values() if e.autonomy.owns(p))
    fake=deepcopy(p);fake.id='not-registered';assert not e.autonomy.allowed(fake,*fake.route[:2])
    e.flow_constraints[f'{s}:{d}:sensor']={'allowed_traffic_classes':[]}
    # Routing category cannot bypass the registered original data flow.
    data_probes=[p for p in e.scheduler.packets.values() if e.autonomy.bindings.get(p.id,{}).get('message',{}).get('original_flow')==(s,d,'sensor')]
    assert data_probes and all(not e.autonomy.allowed(p,*p.route[:2]) for p in data_probes)
    e.configure(MissionConfig(expected_revision=e.revision,affected_uav_assist=False))
    assert not e.adaptive.transition and not e.autonomy.guard and not e.assistance.enabled


def test_modeled_fleet_cost_and_other_affected_bounds():
    e=engine();s,d=evidence(e,1,delivered=0,dropped=8)
    for t in (1,2,3):e.assistance.update(t)
    e.adaptive.evaluate(True);current=e.adaptive.candidates[e.topology];c=deepcopy(current)
    old={'utilities':{s:.5,d:.5},'delivery':.95,'latency_ms':50}
    new={'utilities':{s:.8,d:.5},'delivery':.93,'latency_ms':55}
    assert e.assistance.bound(c,current,new,old,[s,d]) is None
    for change,reason in [({'delivery':.929},'Fleet delivery'),({'latency_ms':55.01},'Fleet latency'),({'utilities':{s:.8,d:.489}},'Another affected')]:
        assert reason in e.assistance.bound(c,current,new|change,old,[s,d])
    c.score=current.score-.021;assert e.assistance.bound(c,current,new,old,[s,d])=='Fleet score cost'


def test_relevant_restriction_change_cancels_before_receipts_and_never_releases_safety():
    e=engine();e.generate_traffic=lambda:None;s,d=stage(e)
    e.flow_constraints[f'{s}:{d}:sensor']={'allowed_traffic_classes':[]}
    e.assistance.permissions_changed();assert not e.adaptive.transition and not e.autonomy.guard
    assert e.assistance.history[-1]['status']=='Cancelled'
    assert not e.flight_safety.supervisors[s].latched


def test_asymmetric_response_loss_never_confirms_replacement():
    e=engine();e.generate_traffic=lambda:None;stage(e)
    original=e.packet_allowed
    def allowed(p,a,b):
        binding=e.autonomy.bindings.get(p.id)
        if binding and binding['message']['kind']=='response':return False
        return original(p,a,b)
    e.packet_allowed=allowed;run(e,3.5)
    assert not e.assistance.counters['switches'] and e.adaptive.fallback
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)


def test_unreachable_highest_priority_does_not_block_next_affected_target():
    e=engine();ids=list(e.nodes)
    evidence(e,1,delivered=0,dropped=8,source=ids[0],dest=ids[1]);evidence(e,1,delivered=0,dropped=8,source=ids[2],dest=ids[3])
    for at in (1,2,3):e.assistance.update(at)
    e.topology_mode='auto';e.adaptive.evaluate(True);old=e.adaptive.candidates[e.topology]
    # Independent selector fixture: same fleet cost, service increase for second target.
    for c in e.adaptive.candidates.values():
        c.score=old.score;c.penalty=0
        for flow,m in c.services.items():
            m.update(reliability=.5,delay_ms=50,capacity=.5)
        c.services.pop((ids[0],'base','heartbeat'),None)
    c=e.adaptive.candidates['partial_mesh']
    for flow,m in c.services.items():
        if ids[2] in flow[:2]:m.update(reliability=1,delay_ms=1,capacity=1)
    e.assistance.choose(e.adaptive.candidates,old)
    assert e.assistance.proposal and e.assistance.proposal['node']==ids[2]


def test_ordinary_optimization_cannot_regress_an_already_affected_uav():
    e=engine();s,d=evidence(e,1,delivered=0,dropped=8)
    for at in (1,2,3):e.assistance.update(at)
    e.adaptive.evaluate(True);old=e.adaptive.candidates[e.topology];c=deepcopy(old);c.topology='ring'
    for m in c.services.values():m.update(reliability=0,delay_ms=1000,capacity=0)
    assert c not in e.assistance.ordinary_allowed({old.topology:old,'ring':c},old)


def test_recoverable_individual_fixture_delivers_measured_gain_below_normal_fleet_threshold():
    from scripts.run_uav_assistance import fixture
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text());results=[]
    for enabled in (False,True):
        e,spec,offers,pairs=fixture(cfg,'recoverable_mesh',enabled,'balanced_v1',7)
        for step in range(700):
            if step==120:e.configure(MissionConfig(expected_revision=e.revision,topology_mode='auto'))
            run(e,.05)
        results.append(e)
    off,on=results
    assert off.topology=='tree' and on.topology=='full_mesh'
    a=on.assistance.history[-1]
    assert a['status']=='Quality improved' and a['latency_ms']<a['before']['latency_ms']*.8
    assert -.02<=a['estimates']['fleet_net_score_difference']<.08
    assert a['estimates']['fleet_delivery_difference']>=-.02
    assert a['estimates']['fleet_latency_ratio']<=1.10
    assert a['verified_routes']>=2 and a['minimum_probe_spacing_s']>=1-1e-9
    assert a['verified_at']-a['started_at']<=3+1e-9
    assert not on.autonomy.enabled and not any(s.latched for s in on.flight_safety.supervisors.values())


def test_staging_random_stream_metadata_is_bounded_with_autonomy_off():
    e=engine();e.generate_traffic=lambda:None;stage(e);run(e,8)
    assert not e.autonomy.enabled and not e.autonomy.channel_sequences


def test_emergency_recovery_bypasses_assistance_regression_filter(monkeypatch):
    e=engine(topology='ring');e.topology_mode='auto'
    # A structural broken ring with physically available replacement edges.
    e.block_link(*list(e.links)[0]);e.rebuild(adapt=False)
    e.adaptive.evaluate(True);incumbent=e.adaptive.candidates[e.topology]
    monkeypatch.setattr(e.assistance,'ordinary_allowed',lambda candidates,current:[current])
    e.adaptive.loss_rebuilds=1;e.adaptive.last_evaluation=-10
    # Sever another ring edge; the physical graph remains connected.
    edges=[edge for edge in e.links if 'base' not in edge and edge not in e.blocked]
    e.block_link(*edges[len(edges)//2]);e.rebuild()
    assert e.adaptive.transition and e.adaptive.transition.get('emergency')


@pytest.mark.parametrize('speed',[.1,.5,1,2,4])
def test_probe_deadline_and_monitor_use_simulation_clock_at_all_speeds(speed):
    e=engine();e.generate_traffic=lambda:None;stage(e);e.controller_connected=False;e.speed=speed
    start=e.seconds;e.playing=True
    for _ in range(round(3.5/(.05*speed))):e.tick(.05)
    assert e.seconds-start==pytest.approx(3.5,abs=.05*speed)
    assert e.assistance.history[-1]['status']=='Transition failed'
    assert not e.autonomy.guard and e.topology=='full_mesh'


def test_assistance_can_share_verifier_with_autonomy_without_releasing_existing_safety_hold():
    from simulation.swarm.flight_safety import FlightFrame
    e=engine(autonomy_mode='recovery_v1');e.generate_traffic=lambda:None;s,d=stage(e)
    supervisor=e.flight_safety.supervisors[s];node=e.nodes[s]
    supervisor.step(FlightFrame(e.seconds,tuple(node.position),(0.,0.,0.),1.,True,e.seconds,False,(),e.seconds),True,'PARTIALLY_DISCONNECTED')
    assert supervisor.latched
    run(e,3.5)
    assert supervisor.latched and node.mode=='safety_hold'
    assert e.autonomy.counters['guard_started']>=1
    assert not any(h['status']=='Quality improved' for h in e.assistance.history)


def test_unknown_pre_switch_quality_is_not_fabricated_as_zero_delivery():
    e=engine();a=e.assistance
    a.attempt=dict(node=list(e.nodes)[0],target='ring',started_at=0,committed_at=1,before=dict(pdr=None,latency_ms=None,completed=0),completed_after=8,delivered_after=8,latency_sum_after=80.)
    a.realized(11)
    assert a.history[-1]['pdr']==1 and a.history[-1]['status']=='Receipt evidence available; baseline quality unknown'
    a.attempt=deepcopy(a.history[-1]);a.configure();assert a.attempt is None and a.history[-1]['status']=='Assessment cancelled'


def test_fleet_changes_retire_old_monitor_identities_and_traffic():
    e=engine(default_count=20);old=list(e.nodes)[-1];other=list(e.nodes)[0]
    evidence(e,1,delivered=0,dropped=8,source=old,dest=other)
    for t in (1,2,3):e.assistance.update(t)
    e.configure(MissionConfig(expected_revision=e.revision,count=10));e.assistance.update(4)
    assert old not in e.assistance.nodes and not any(old in flow[:2] for flow in e.assistance.flows)
    assert not e.assistance.allowed((old,other,'sensor'))


def test_mesh_interface_failures_and_revoked_permissions_are_explicit():
    e=engine();n=list(e.nodes)[0];e.autonomy.interfaces[n]={'mesh':False};e.assistance.update(1)
    assert e.assistance.nodes[n]['status']=='Mesh interface unavailable' and not e.assistance.nodes[n]['degraded']
    e.autonomy.revoked.add(n);e.assistance.update(2)
    assert e.assistance.nodes[n]['status']=='Policy blocked'
