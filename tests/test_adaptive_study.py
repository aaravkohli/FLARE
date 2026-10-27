"""Independent protocol/statistics assertions and authoritative scheduler checks."""
from copy import deepcopy
import json
from pathlib import Path
from queue import Queue
import pytest
from pydantic import ValidationError
import yaml
from schemas.mission import AdaptiveStudyRequest, ExperimentRequest, TOPOLOGIES
from simulation.swarm.adaptive_study import protocol, run_one, scientific_rows, digest, storage_preflight, export_study, study_worker
from simulation.swarm.experiments import reference_engine
from simulation.swarm.study_statistics import paired_summary

def cfg(): return yaml.safe_load(Path('config/swarm_config.yaml').read_text())

def rows():
    return [dict(protocol_id='adaptive_resilience_v1',reference_workload='mesh_peer_reference_v3',scenario='normal',seed=seed,
        policy_id=policy,pdr=value,fleet_sha256='fleet',shared_input_sha256=f'input-{seed}',service_recovery_ms=None,unrecovered_flows=2)
        for seed,value in ((1,.8),(2,.9),(3,1.)) for policy,value in (('auto',value),('self_healing_mesh',value-.1))]

def test_protocol_is_held_out_precise_and_not_mutable_through_request():
    p=protocol('evidence',20)
    assert p['total_runs']==550 and p['seeds']==list(range(1001,1011))
    assert p['total_simulation_s']==85 and p['traffic_stop_s']-p['warmup_s']==60
    assert protocol('pilot',20)['total_runs']==165
    assert 'auto' not in TOPOLOGIES
    with pytest.raises(ValueError): protocol('evidence',10)
    with pytest.raises(ValidationError): AdaptiveStudyRequest(mode='adaptive_study',protocol_id='adaptive_resilience_v1',preset='evidence',count=10)
    with pytest.raises(ValidationError): AdaptiveStudyRequest(mode='adaptive_study',protocol_id='bogus',preset='pilot')

def test_independent_constant_paired_difference_and_reproducible_bootstrap():
    r=paired_summary(rows(),'normal','self_healing_mesh')
    assert r['mean_difference']==pytest.approx(10)
    assert r['interval_95']==pytest.approx([10,10]) and r['valid_pairs']==3
    assert r==paired_summary(rows(),'normal','self_healing_mesh')
    assert r['comparison']=='primary' and r['resamples']==10000

def test_bootstrap_nonconstant_interval_contains_independently_enumerated_bounds():
    r=rows();r[0]['pdr']=.5;r[4]['pdr']=.95
    stats=paired_summary(r,'normal','self_healing_mesh')
    assert stats['mean_difference']==pytest.approx((-20+10+5)/3)
    assert stats['interval_95']==pytest.approx([-20,10])

@pytest.mark.parametrize('field,value',[('protocol_id','other'),('reference_workload','v2'),('fleet_sha256','other'),('shared_input_sha256','other')])
def test_incompatible_pairs_are_rejected(field,value):
    r=rows();r[0][field]=value
    with pytest.raises(ValueError,match='incompatible'):paired_summary(r,'normal','self_healing_mesh')

def test_null_recovery_unrecovered_and_partial_pairs_are_not_zeroes():
    r=rows()[:3]
    s=paired_summary(r,'normal','self_healing_mesh','service_recovery_ms')
    assert s['valid_pairs']==0 and s['mean_difference'] is None and s['interval_95'] is None
    s=paired_summary(r,'normal','self_healing_mesh')
    assert s['valid_pairs']==1 and s['missing_pairs']==1 and s['interval_95'] is None
    assert paired_summary(rows(),'normal','self_healing_mesh','unrecovered_flows')['mean_difference']==0
    with pytest.raises(ValueError,match='duplicate'):paired_summary(rows()+rows()[:1],'normal','self_healing_mesh')

def test_nominal_reference_demand_exists_before_auto_initializes():
    r=ExperimentRequest(mode='benchmark',count=30,seeds=[7])
    e=reference_engine([],cfg(),r,'self_healing_mesh',7,automatic=True)
    assert not e.adaptive.offered
    rates,evidence=e.adaptive.demands();ids=list(e.nodes)
    assert evidence=='nominal_reference_schedule_estimate' and len(rates)==30
    assert rates[ids[0],ids[15],'sensor']==512
    assert sum(rates.values())==30*512
    assert e.adaptive.last_evaluation==0
    e.seconds=76
    assert e.adaptive.demands()==({},'no_offered_reference_traffic')
    fixed=reference_engine([],cfg(),r,'ring',7)
    assert fixed.topology_mode=='manual'

def test_effective_congestion_is_versioned_peer_load_and_queues_real_radios():
    c=cfg();r=ExperimentRequest(mode='benchmark',count=30,seeds=[7],scenario='congestion',duration_s=30)
    e=reference_engine([],c,r,'star',7); e.playing=True
    counts={'transmissions':0,'overflow':0}; samples=[]
    def sink(event):
        data=event['data']
        if data.get('correlation_id')!='reference_background':return
        if event['kind']=='packet_queued': samples.append((data['source'],data['destination'],data['size_bytes']))
        if event['kind']=='packet_hop':counts['transmissions']+=1
        if event['kind']=='packet_result' and data.get('reason')=='queue_overflow':counts['overflow']+=1
    e.trace_sink=sink
    for _ in range(240):e.tick(.05)
    assert e.reference_traffic.background_offered==2025
    assert len({(a,b) for a,b,_ in samples})==5 and all(b!='base' and n==4096 for _,b,n in samples)
    assert counts['transmissions']>0 and counts['overflow']>0
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)
    assert not e.attacks.active # traffic injection is not detector evidence

def test_timing_is_bounded_and_not_scientific_data():
    from simulation.swarm.performance import Performance
    p=Performance()
    for i in range(300):
        with p.measure('scheduling'):pass
        p.tick(i,i+1,.4)
    assert len(p.samples['scheduling'])==256 and len(p.ticks)==240
    s=p.snapshot(False,1)
    assert s['achieved_speed'] is None and s['status']=='paused'
    assert s['clipped_wall_time_s']==pytest.approx(45)
    assert digest(scientific_rows([{'pdr':0,'timing':1}]))==digest(scientific_rows([{'pdr':0,'timing':99}]))

def test_storage_fails_before_writing_and_partial_exports_are_complete(tmp_path,monkeypatch):
    from collections import namedtuple
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr('simulation.swarm.adaptive_study.shutil.disk_usage',lambda _:usage(100,99,1))
    with pytest.raises(ValueError,match='Insufficient study storage'):storage_preflight(tmp_path,550)
    state=dict(mode='adaptive_study',status='cancelled',protocol=protocol('pilot',20),results=[],statistics=[])
    assert 'Partial retained results' in export_study(state,'md')
    assert json.loads(export_study(state,'json'))['status']=='cancelled'
    assert export_study(state,'csv').startswith('protocol_id,reference_workload')

def test_cancelled_worker_retains_results_and_never_mutates_inputs(tmp_path,monkeypatch):
    # Cancellation is independent of the host's changing free space. The low-
    # storage behavior is exercised separately above, against the real preflight.
    from collections import namedtuple
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr('simulation.swarm.adaptive_study.shutil.disk_usage',lambda _:usage(10*1024**3,0,10*1024**3))
    config=cfg();before=deepcopy(config);q=Queue();q.put('cancel')
    study_worker('test',dict(mode='adaptive_study',protocol_id='adaptive_resilience_v1',preset='pilot'),[],config,tmp_path,q)
    state=json.loads((tmp_path/'state.json').read_text())
    assert state['status']=='cancelled' and state['results']==[] and config==before
    assert (tmp_path/'results.md').exists()

def test_full_run_repeatability_and_cross_arm_external_inputs(tmp_path):
    config=cfg();before=deepcopy(config)
    first=run_one([],config,'normal','ring',7,tmp_path)
    again=run_one([],config,'normal','ring',7,tmp_path)
    other=run_one([],config,'normal','auto',7,tmp_path)
    assert scientific_rows([first])==scientific_rows([again])
    for field in ('shared_input_sha256','fleet_sha256','motion_sha256','offered_input_sha256'):
        assert first[field]==other[field]
    assert first['generated']==1800 and first['conservation'] and other['conservation']
    assert config==before
