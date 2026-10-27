from copy import deepcopy
from pathlib import Path
import json
import pytest
from simulation.swarm.resilience_study import protocol,summaries,export_study,ARMS


def test_pilot_protocol_and_partial_null_pairing():
    p=protocol();assert p['total_runs']==72 and p['seeds']==[7,42,99] and len(p['scenarios'])==6
    assert p['total_simulation_s']==85 and p['model_inference']=='excluded'
    assert protocol(5)['total_simulation_s']==70 and protocol(5)['ttl_drain_s']==5
    assert not p['feature_arms']['balanced_control']['protected_scheduling']
    assert not p['feature_arms']['packet_transition']['protected_scheduling']
    assert p['feature_arms']['observability_guard']['protected_scheduling']
    assert p['feature_arms']['combined']['protected_scheduling']
    rows=[]
    for seed in (7,42,99):
        for arm in ARMS:
            rows.append(dict(protocol_id=p['id'],reference_workload=p['reference_workload'],design_sha256='shared',
                fleet_sha256='fleet',shared_input_sha256=f'shared-{seed}',scenario='normal',seed=seed,policy_id=arm,pdr=.9 if arm=='combined' else .8,
                fresh_evidence_ratio=None if seed==99 else .5))
    a=summaries(rows);assert a==summaries(rows)
    delivery=next(r for r in a if r['scenario']=='normal' and r['baseline']=='balanced_control' and r['metric']=='pdr')
    assert delivery['mean_difference']==pytest.approx(10) and delivery['valid_pairs']==3 and delivery['comparison']=='exploratory'
    freshness=next(r for r in a if r['scenario']=='normal' and r['baseline']=='balanced_control' and r['metric']=='fresh_evidence_ratio')
    assert freshness['valid_pairs']==2 and freshness['missing_pairs']==1
    incompatible=deepcopy(rows);incompatible[0]['shared_input_sha256']='wrong'
    with pytest.raises(ValueError,match='incompatible'):summaries(incompatible)
    state=dict(protocol=p,status='cancelled',results=rows,statistics=a)
    assert len(json.loads(export_study(state,'json'))['results'])==12
    assert 'cancelled' in export_study(state,'md')
    assert len(export_study(state,'csv').splitlines())==13


def test_recovery_requires_a_condition_period_gap_and_retains_unrecovered():
    from simulation.swarm.resilience_analysis import EvidenceRecovery
    normal=EvidenceRecovery('normal');normal.observe(10,0);normal.observe(25,1)
    assert normal.value is None and not normal.unrecovered
    late=EvidenceRecovery('congestion');late.observe(30,0);late.observe(32,1)
    assert late.value is None and not late.unrecovered
    actual=EvidenceRecovery('regional_jamming');actual.observe(8,.5);actual.observe(25,None)
    assert actual.value is None and actual.unrecovered
    actual.observe(26,1);actual.observe(27,1)
    assert actual.value==1000 and not actual.unrecovered


def test_offline_continuation_validates_source_protocol_and_trace(tmp_path):
    import hashlib
    from scripts.resume_resilience_pilot import validate
    from simulation.swarm.experiments import source_fingerprint
    trace=tmp_path/'normal-balanced_control-7.jsonl.gz';trace.write_bytes(b'bounded fixture')
    original=dict(status='failed',source_sha256=source_fingerprint(),simulation_config={'packets':{'ttl_s':20}},protocol=protocol(),
        results=[dict(scenario='normal',seed=7,policy_id='balanced_control',trace_file=trace.name)],
        artifact_manifest=[dict(file=trace.name,sha256=hashlib.sha256(trace.read_bytes()).hexdigest())])
    before=deepcopy(original);design,retained=validate(original,tmp_path)
    assert design==protocol() and retained=={('normal',7,'balanced_control')} and original==before
    for field,value in [('source_sha256','different'),('protocol',{}),('status','completed')]:
        changed=deepcopy(original);changed[field]=value
        with pytest.raises(ValueError):validate(changed,tmp_path)
    trace.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='hash mismatch'):validate(original,tmp_path)
