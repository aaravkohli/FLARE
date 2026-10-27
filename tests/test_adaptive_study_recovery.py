"""Offline continuation validation; fixtures test integrity, not simulated outcomes."""
from copy import deepcopy
import hashlib
import pytest
from scripts.resume_adaptive_study import validate_retained, projected_storage
from simulation.swarm.adaptive_study import protocol
from simulation.swarm.experiments import source_fingerprint


def previous():
    return dict(status='failed',source_sha256=source_fingerprint(),configuration={'preset':'pilot'},
        simulation_config={'packets':{'ttl_s':20}},protocol=protocol('pilot',20),results=[],artifact_manifest=[])


def test_continuation_validates_without_mutating_interrupted_state(tmp_path):
    p=previous();before=deepcopy(p)
    specification,retained=validate_retained(p,tmp_path)
    assert specification['total_runs']==165 and retained==set() and p==before


@pytest.mark.parametrize('change',[{'source_sha256':'other'},{'status':'running'},{'status':'completed'}])
def test_continuation_rejects_changed_source_and_noninterrupted_jobs(tmp_path,change):
    with pytest.raises(ValueError):validate_retained(previous()|change,tmp_path)


def test_continuation_rejects_trace_corruption_and_duplicate_observations(tmp_path):
    p=previous();name='normal-auto-7.jsonl.gz';payload=b'integrity fixture only'
    (tmp_path/name).write_bytes(payload)
    p['results']=[dict(scenario='normal',seed=7,policy_id='auto',trace_file=name)]
    p['artifact_manifest']=[dict(file=name,sha256=hashlib.sha256(payload).hexdigest())]
    assert validate_retained(p,tmp_path)[1]=={('normal',7,'auto')}
    (tmp_path/name).write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='hash mismatch'):validate_retained(p,tmp_path)
    p['results']*=2
    with pytest.raises(ValueError,match='identities'):validate_retained(p,tmp_path)


def test_storage_projection_uses_policy_and_offered_volume_without_numerical_outcomes(tmp_path,monkeypatch):
    from collections import namedtuple
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr('scripts.resume_adaptive_study.shutil.disk_usage',lambda _:usage(10**10,0,10**10))
    p=protocol('pilot',20)
    rows=[dict(policy_id='ring',trace_file='ring',packet_accounting={'generated':1950},pdr=.2),
          dict(policy_id='star',trace_file='star',packet_accounting={'generated':1950},pdr=.9)]
    artifacts=[dict(file='ring',bytes=19500),dict(file='star',bytes=1950)]
    jobs=[('normal',7,'ring'),('congestion',7,'star')]
    result=projected_storage(tmp_path,jobs,rows,artifacts,p)
    assert result['projected_required_bytes']==int((19500+10450)*1.5+512*1024**2)
    rows[0]['pdr']=1
    assert projected_storage(tmp_path,jobs,rows,artifacts,p)==result
    monkeypatch.setattr('scripts.resume_adaptive_study.shutil.disk_usage',lambda _:usage(100,99,1))
    with pytest.raises(ValueError,match='storage'):projected_storage(tmp_path,jobs,rows,artifacts,p)
