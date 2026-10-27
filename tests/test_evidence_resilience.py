"""Independent packet, observation and policy contract checks for opt-in control."""
from copy import deepcopy
from pathlib import Path
import json
import random
import pytest
import yaml
from schemas.mission import MissionConfig,MissionCommand,MissionControl,RoutePolicy,AdaptiveStudyRequest
from simulation.swarm.engine import SwarmEngine,RevisionConflict
from simulation.swarm.network import PacketScheduler,Link
from simulation.swarm.topology import pair
from simulation.swarm.experiments import reference_engine
from schemas.mission import ExperimentRequest


def engine(**changes):
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg.update(default_count=10,topology_mode='manual',topology='full_mesh',adaptive_policy='evidence_resilience_v1')
    cfg.update(changes);cfg['radio'].update(range_m=1000,reference_rssi_dbm=-20,noise_dbm=-110)
    return SwarmEngine([],cfg,isolated=True)


def run(e,seconds):
    e.playing=True
    for _ in range(round(seconds/.05)): e.tick(.05)


def test_opt_in_legacy_manual_reset_and_reference_pin():
    legacy=SwarmEngine([],isolated=True);assert not legacy.observability.enabled
    assert legacy.metrics() and legacy.scheduler.generated==0
    e=engine();assert not e.metrics();run(e,1)
    assert e.metrics()
    e.configure(MissionConfig(expected_revision=e.revision,topology='ring'))
    e.load_scenario('mobility',e.revision);assert e.cfg['adaptive_policy']=='evidence_resilience_v1' and e.topology=='ring'
    e.reset();assert not e.metrics() and not e.resilience.leases
    fixed=reference_engine([],e.cfg,ExperimentRequest(), 'ring',7)
    assert not fixed.observability.enabled and not fixed.scheduler.protected_scheduling and not fixed.cfg.get('observation_base_endpoint')
    with pytest.raises(ValueError): AdaptiveStudyRequest(mode='adaptive_study',protocol_id='evidence_resilience_pilot_v1',preset='evidence')


def test_reports_require_complete_receipts_and_frozen_samples():
    e=engine();e.cfg['resilience']['fragment_bytes']=128
    e.observability.step();assert not e.metrics()
    report=next(iter(e.observability.pending.values()));assert report['remaining']>1
    frozen=deepcopy(report['payload']);identity=report['node']
    e.observability.result(dict(id='forged',correlation_id=report['id'],status='delivered',completed_at=0))
    assert report['remaining']>1
    run(e,.5);receipt=e.observability.latest[identity]
    assert receipt['payload']==frozen
    assert 'ew_status' not in receipt['payload'] and 'simulation_profile' not in receipt['payload']['security_evidence']
    assert e.metrics()[identity]['observation']['received_at']>=receipt['captured_at']
    assert sum(p.size_bytes for p in e.scheduler.packets.values() if p.correlation_id==report['id'])<=report['size_bytes']


def test_age_pause_expiry_and_policy_epoch_do_not_refresh_samples():
    e=engine(observation_stop_s=.1);run(e,.5);node=next(iter(e.observability.latest))
    captured=e.observability.latest[node]['captured_at'];assert e.metrics()[node]['timestamp']==1700000000.+captured
    e.control(MissionControl(expected_revision=e.revision,action='pause'));age=e.observability.statuses()[node]['age_s']
    for _ in range(20): e.tick(.25)
    assert e.observability.statuses()[node]['age_s']==age
    run(e,3);assert node not in e.metrics() and e.observability.statuses()[node]['status']=='Stale'
    e.configure(MissionConfig(expected_revision=e.revision,adaptive_policy='balanced_v1'))
    assert e.metrics() and not e.observability.latest
    with pytest.raises(RevisionConflict):e.configure(MissionConfig(expected_revision=e.revision-1,adaptive_policy='evidence_resilience_v1'))


def test_candidate_checks_are_side_effect_free_and_respect_partitions():
    e=engine();run(e,.3)
    before=deepcopy((e.scheduler.radio_busy,e.scheduler.packets,e.nodes,e.links,e.flows,e.rng.getstate()))
    e.adaptive.evaluate(True)
    assert before==(e.scheduler.radio_busy,e.scheduler.packets,e.nodes,e.links,e.flows,e.rng.getstate())
    assert len(e.adaptive.candidates)==10
    node=next(iter(e.nodes));e.contain(node,'quarantined','test')
    for candidate in e.adaptive.candidates.values():
        assessment=e.resilience.assess(candidate);assert node not in assessment['feasible_reports']
    assert e.observability.statuses()[node]['status']=='Quarantined'


def test_registration_not_category_grants_protected_service():
    e=engine();node=next(iter(e.nodes))
    fake=e.send(node,'base','threat',64,'fake',service={'kind':'security_report','deadline':1})
    assert fake.service_kind is None
    command=e.command(MissionCommand(session_id=e.session_id,expected_revision=e.revision,command_id='real',targets=[node],action='role',parameters={'role':'standard'}))
    e.playing=True;e.dispatch_commands()
    real=next(p for p in e.scheduler.packets.values() if p.correlation_id=='real' and p.category=='command')
    assert real.service_kind=='command' and real.service_deadline==command['expires_at']


def test_transition_leases_only_captured_packets_and_bound_duration():
    e=engine();ids=list(e.nodes);p=e.send(ids[0],ids[5],'sensor',4096)
    original=p.route[:];e.adaptive.request('ring','test');assert e.adaptive.transition['enhanced']
    e.rebuild();assert e.adaptive.transition['phase']=='overlap' and e.topology=='full_mesh'
    assert p.route==original and e.resilience.authorizes(p)
    outsider=deepcopy(p);outsider.id='not-captured';assert not e.resilience.authorizes(outsider)
    lease=e.resilience.leases[p.id];e.scheduler.checking_at=lease['expires_at'];assert not e.resilience.authorizes(p)
    e.scheduler.checking_at=e.seconds
    e.flow_constraints[f'{p.source}:{p.destination}:{p.category}']={'excluded_nodes':[p.destination]}
    assert not e.resilience.authorizes(p)
    e.flow_constraints.clear();run(e,1.5)
    assert not e.resilience.leases and e.topology=='ring'
    assert e.resilience.last_plan['overlap_ended_at']<=1.+1e-8
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)


def test_containment_overrides_retained_forwarding_and_rollback_never_rewinds(monkeypatch):
    e=engine();ids=list(e.nodes);p=e.send(ids[0],ids[5],'command',128)
    e.adaptive.request('ring','test');e.rebuild();assert p.id in e.resilience.leases
    e.contain(p.destination,'quarantined','test');assert not e.resilience.authorizes(p)
    generated=e.scheduler.generated;e.adaptive.fail('injected install failure')
    assert not e.resilience.leases and e.scheduler.generated==generated and e.topology=='full_mesh'
    assert e.adaptive.blacklist['ring']==30


def test_priority_reservation_is_work_conserving_and_nonpreemptive():
    events=[];s=PacketScheduler(dict(ttl_s=3,retries=0,capacity=100,queue_limit=64,seed=7),random.Random(7),lambda k,d:events.append((k,d)))
    s.protected_scheduling=True;link=Link('a','b',1,-40,30,.08,0,0)
    ordinary=s.emit('a','b','sensor',1000,['a','b'],0,0)
    protected=s.emit('a','b','threat',100,['a','b'],0,0,service={'kind':'registered','deadline':1})
    s.step(.2,{pair('a','b'):link},lambda *_:True)
    starts=[d for k,d in events if k=='packet_hop'];assert starts[0]['id']==protected.id
    assert starts[1]['id']==ordinary.id and starts[1]['start_at']==pytest.approx(.01)
    assert starts[1]['end_at']==pytest.approx(.11)
    assert s.generated==s.delivered+s.dropped+len(s.packets)
    assert all(len(v)<=21 for v in s.airtime_usage.values())
    unreserved=PacketScheduler(dict(ttl_s=3,retries=0,capacity=100,queue_limit=64,seed=7),random.Random(7),lambda *_:None)
    unreserved.emit('a','b','sensor',1000,['a','b'],0,0)
    unreserved.emit('a','b','threat',100,['a','b'],0,0,service={'kind':'registered','deadline':1})
    unreserved.step(.2,{pair('a','b'):link},lambda *_:True)
    assert unreserved.airtime_totals==s.airtime_totals
    assert unreserved.airtime_totals['protected']==pytest.approx(.02)
    assert unreserved.airtime_totals['ordinary']==pytest.approx(.2)


def test_channel_keys_do_not_depend_on_conditional_other_offers():
    a=engine();b=engine();ids=list(a.nodes)
    b.send(ids[2],ids[3],'ack',64,'conditional')
    p=a.send(ids[0],ids[1],'sensor',512,'primary');q=b.send(ids[0],ids[1],'sensor',512,'primary')
    assert p.channel_key==q.channel_key and p.sequence!=q.sequence
    assert a.scheduler.draw(p,pair(ids[0],ids[1]),'loss')==b.scheduler.draw(q,pair(ids[0],ids[1]),'loss')


@pytest.mark.parametrize('case',json.loads(Path('tests/fixtures/resilience_balanced_baseline.json').read_text()))
def test_fresh_prechange_fixed_workload_numerical_baselines(case):
    from simulation.swarm.experiments import summarize
    expected=case['row'];cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg['adaptive_policy']='evidence_resilience_v1' # copied live intent must not leak
    request=ExperimentRequest(mode='benchmark',topologies=[expected['topology']],count=10,scenario=case['scenario'],duration_s=10,warmup_s=1,seeds=[7])
    e=reference_engine([],cfg,request,expected['topology'],7);cohort=[]
    e.trace_sink=lambda event:cohort.append(event['data']) if event['kind']=='packet_result' and event['data'].get('correlation_id')=='reference' else None
    run(e,30);row=summarize(e,request,expected['topology'],7,cohort);row.pop('configuration_sha256')
    assert row==expected


@pytest.mark.parametrize('speed',[.1,.25,.5,1,2,4])
def test_report_age_uses_simulation_clock_at_every_speed(speed):
    e=engine(observation_stop_s=.1);run(e,.5)
    e.speed=speed;report=next(iter(e.observability.latest.values()));before=e.seconds
    e.tick(.25)
    assert e.seconds-before==pytest.approx(int((.25*speed+1e-9)/.05)*.05)
    row=e.observability.statuses()[report['node']]
    assert row['age_s']==pytest.approx(e.seconds-report['captured_at'])


def test_new_policies_cannot_use_retained_edges():
    e=engine();ids=list(e.nodes);p=e.send(ids[0],ids[5],'sensor',512)
    e.adaptive.request('ring','test');e.rebuild()
    result=e.install_policy(RoutePolicy(policy_id='new',session_id=e.session_id,revision=e.revision,source=p.source,destination=p.destination,traffic_class='sensor',family='mesh',hops=p.route))
    assert not result['applied'] and result['reason']=='retained_edges_are_packet_specific'
    q=e.send(p.source,p.destination,'sensor',512)
    assert all(pair(a,b) in e.resilience.target_links for a,b in zip(q.route,q.route[1:]))


def test_missing_report_start_and_unreachable_do_not_fabricate_freshness():
    e=engine();ids=list(e.nodes)
    e.blocked.update(pair(n,'base') for n in ids);e.rebuild();run(e,.5)
    assert not e.metrics()
    assert all(r['status']=='Unreachable' for r in e.observability.statuses().values())
    e.nodes[ids[0]].available=False
    assert e.observability.statuses()[ids[0]]['status']=='Unavailable'


def test_orchestrator_processes_a_report_once_and_holds_when_unavailable(monkeypatch,make_orchestrator):
    import asyncio
    from types import SimpleNamespace
    import orchestrator.loop as module
    runtime=make_orchestrator(['drone_1'],freeze_fleet=True);rows=[];held=[]
    e=engine();run(e,.5);sample=deepcopy(next(iter(e.metrics().values())));sample['drone_id']='drone_1'
    current={'drone_1':sample}
    async def collect(_):return deepcopy(current)
    async def push(_,family,identity,context=None):return {'status':'ok','installed_path':family}
    async def hold(_,ids):held.extend(ids)
    runtime._collect_metrics=collect;runtime._fail_closed_unavailable_drones=hold
    monkeypatch.setattr(module,'_push_to_sdn',push);monkeypatch.setattr(module,'_log_experiments',rows.extend)
    for _ in range(2):asyncio.run(runtime._run_step(SimpleNamespace()))
    assert len(rows)==1 and len(runtime._metric_history['drone_1'])==1 and not held
    current.clear();asyncio.run(runtime._run_step(SimpleNamespace()));assert held==['drone_1'] and len(rows)==1


@pytest.mark.parametrize('seed',[7,42,99])
@pytest.mark.parametrize('count',range(10,101,10))
def test_enhanced_ten_candidate_matrix(count,seed):
    e=engine(default_count=count,seed=seed)
    before=deepcopy((e.nodes,e.links,e.flows,e.scheduler.packets,e.scheduler.radio_busy,e.rng.getstate()))
    e.adaptive.evaluate(True);first=[c.diagnostic() for c in e.adaptive.candidates.values()]
    e.adaptive.evaluate(True)
    assert first==[c.diagnostic() for c in e.adaptive.candidates.values()]
    assert len(first)==10 and all(c['resilience']['basis']=='modeled_queue_radio_and_retry_estimates' for c in first)
    assert before==(e.nodes,e.links,e.flows,e.scheduler.packets,e.scheduler.radio_busy,e.rng.getstate())


def test_normal_candidate_rejection_preserves_current_feasible_reports(monkeypatch):
    e=engine()
    def assess(c):return dict(feasible_reports={'protected'} if c.topology==e.topology else set(),
        missed_packets=set(),protected_packets=0,reachable_reports={'protected'},maximum_report_utilization=.01,basis='test_fixture')
    monkeypatch.setattr(e.resilience,'assess',assess);e.adaptive.evaluate(True)
    assert all(c.service_admissible==(c.topology==e.topology) for c in e.adaptive.candidates.values())
    assert e.adaptive.best().topology==e.topology


def test_expiring_binding_does_not_erase_an_earlier_precise_arrival():
    e=engine();ids=list(e.nodes);p=e.send(ids[0],ids[5],'sensor',128)
    e.adaptive.request('ring','test');e.rebuild();assert p.id in e.resilience.leases
    # Bound earlier than the movement step, but later than the actual arrival.
    e.resilience.leases[p.id]['expires_at']=.04
    e.scheduler.step(.05,e.packet_link,e.packet_allowed,e.attacks.relay_drop,e.repair_packet)
    assert p.status=='delivered' and p.completed_at<.04 and p.history


def test_cohort_scheduler_optimization_matches_independent_ordering():
    from scripts.profile_resilience_scheduler import sample
    _,reference=sample(True,count=40);_,optimized=sample(False,count=40)
    assert reference==optimized


def test_duplicate_lost_and_out_of_order_fragments_and_epoch_invalidation():
    e=engine(observation_stop_s=0);e.cfg['resilience']['fragment_bytes']=128
    node=next(iter(e.nodes));payload=e.raw_metrics()[node]
    def capture():
        e.observability.capture(node,payload)
        return list(e.observability.pending.values())[-1]
    def finish(report,lost=False):
        fragments=[p for p in e.scheduler.packets.values() if p.correlation_id==report['id']]
        for index,p in enumerate(fragments):
            result=dict(id=p.id,correlation_id=p.correlation_id,channel_key=p.channel_key,
                status='dropped' if lost and index==0 else 'delivered',completed_at=e.seconds+.01)
            e.observability.result(result)
            e.observability.result(result) # A duplicate cannot complete an extra fragment.
    first=capture();e.seconds=.5;second=capture();finish(second)
    assert e.observability.latest[node]['id']==second['id']
    finish(first);assert e.observability.latest[node]['id']==second['id']
    assert e.observability.counters['out_of_order']==1
    e.seconds=1.;failed=capture();finish(failed,lost=True)
    assert e.observability.latest[node]['id']==second['id'] and e.observability.counters['failed']==1
    pending=capture();e.observability.invalidate();finish(pending)
    assert not e.observability.latest and not e.metrics()


def test_enhanced_installation_failure_rolls_back_current_state(monkeypatch):
    e=engine();ids=list(e.nodes);packet=e.send(ids[0],ids[5],'sensor',512)
    route=packet.route[:];generated=e.scheduler.generated
    e.adaptive.request('ring','operator')
    original=e.ensure_route;failures=[]
    def install(*args,**kwargs):
        if e.adaptive.transition and not failures:
            failures.append(True);raise RuntimeError('test forwarding installation failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(e,'ensure_route',install);e.rebuild()
    assert failures and e.adaptive.transition is None and e.topology=='full_mesh'
    assert e.adaptive.last_transition=='failed' and not e.resilience.leases
    assert e.scheduler.generated==generated and packet.route==route
    assert e.adaptive.blacklist['ring']==30 and e.resilience.last_plan['status']=='failed'


@pytest.mark.parametrize('seed',[7,42,99])
def test_cohort_order_matches_reference_across_contended_radios(seed):
    from scripts.profile_resilience_scheduler import reference_pop
    def sample(reference):
        events=[];rng=random.Random(seed)
        scheduler=PacketScheduler(dict(ttl_s=3,retries=2,capacity=500,queue_limit=100,seed=seed,jitter_ms=2),random.Random(seed),lambda k,d:events.append((k,d)))
        scheduler.protected_scheduling=True
        if reference:scheduler.pop_event=lambda:reference_pop(scheduler)
        links={pair(str(a),str(b)):Link(str(a),str(b),1,-40,30,.5,.12,1) for a in range(6) for b in range(a+1,6)}
        for index in range(90):
            path=rng.sample([str(i) for i in range(6)],3);at=rng.choice([0,.005,.012])
            scheduler.emit(path[0],path[-1],'threat' if index%3==0 else 'sensor',rng.choice([128,512,4096]),path,at,0,
                service={'kind':'registered','deadline':2} if index%3==0 else None)
        scheduler.step(3,links,lambda *_:True)
        return events
    assert sample(True)==sample(False)


@pytest.mark.parametrize('policy',['balanced_v1','evidence_resilience_v1'])
def test_deferred_admission_uses_original_offer_order_not_set_iteration(policy):
    class ReversedSet(set):
        def __iter__(self):return iter(sorted(super().__iter__(),reverse=True))
    e=engine(adaptive_policy=policy);ids=list(e.nodes);e.adaptive.transition={'phase':'draining'}
    offered=[e.send(ids[i%10],ids[(i+5)%10],'sensor',512).id for i in range(12)]
    assert set(offered)==e.scheduler.deferred
    e.scheduler.deferred=ReversedSet(e.scheduler.deferred)
    e.adaptive.release_deferred()
    released=[row['data']['id'] for row in e.packet_events if row['kind']=='packet_released']
    assert released==offered and not e.scheduler.deferred
