"""Continue missing study runs in a new directory; retain the interrupted job.

This offline recovery does not add pause/resume controls to API study batches.
Completed, hash-validated observations are reused only with identical numerical
source and protocol. No selector settings are changed or evidence seeds tuned.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import time
import uuid
from simulation.swarm.adaptive_study import (ARMS, SCENARIOS, _parallel_run, digest, export_study,
    protocol, scientific_rows, storage_preflight)
from simulation.swarm.experiments import _atomic_json, source_fingerprint
from simulation.swarm.study_statistics import statistical_report
from schemas.mission import TOPOLOGIES


def projected_storage(directory,remaining,rows,artifacts,p):
    """Stratify by policy and offered volume instead of a global trace maximum.

    Use the highest observed bytes/offer for each policy across all cases,
    50% headroom and 512 MiB reserve. The numerical worker's low-space guard
    remains active. This planning calculation cannot affect packet outcomes.
    """
    by_file={a['file']:a['bytes'] for a in artifacts};rates={}
    for row in rows:
        offered=row['packet_accounting']['generated']
        if offered:rates[row['policy_id']]=max(rates.get(row['policy_id'],0),by_file[row['trace_file']]/offered)
    primary=p['count']*int(p['traffic_stop_s'])
    projected=sum(rates.get(arm,8*1024**2/primary)*(primary+(8500 if scenario=='congestion' else 0)) for scenario,_,arm in remaining)
    free=shutil.disk_usage(directory).free;required=int(projected*1.5+512*1024**2)
    if free<required:raise ValueError(f'Insufficient continuation storage: {free/1024**3:.2f} GiB free; {required/1024**3:.2f} GiB projected including reserve')
    return dict(free_bytes=free,projected_required_bytes=required,projection='maximum policy bytes per offer × remaining offered volume × 1.5 + 512 MiB reserve')


def validate_retained(previous, source_directory):
    if previous.get('status') not in ('failed','cancelled'): raise ValueError('Only terminal interrupted jobs can be continued')
    if previous['source_sha256']!=source_fingerprint(): raise ValueError('Numerical source changed; run a new study instead')
    p=protocol(previous['configuration']['preset'],previous['simulation_config']['packets']['ttl_s'])
    if previous['protocol']!=p: raise ValueError('Protocol changed; refusing to combine observations')
    expected={(s,seed,a) for s in p['scenarios'] for seed in p['seeds'] for a in p['policies']}
    retained={(r['scenario'],r['seed'],r['policy_id']) for r in previous['results']}
    if len(retained)!=len(previous['results']) or not retained<=expected: raise ValueError('Invalid retained run identities')
    artifacts={a['file']:a for a in previous['artifact_manifest']}
    for row in previous['results']:
        name=row['trace_file']
        if Path(name).name!=name or name not in artifacts: raise ValueError('Invalid retained trace')
        with (source_directory/name).open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        if checksum!=artifacts[name]['sha256']: raise ValueError('Retained trace hash mismatch')
    return p,retained


def resume(source_directory,directory,workers=2):
    previous=json.loads((source_directory/'state.json').read_text());p,retained=validate_retained(previous,source_directory)
    missing=[(s,seed,a) for s in p['scenarios'] for seed in p['seeds'] for a in p['policies'] if (s,seed,a) not in retained]
    directory.mkdir(parents=True,exist_ok=False)
    sizes=[a['bytes'] for a in previous['artifact_manifest']]
    storage=projected_storage(directory,missing,previous['results'],previous['artifact_manifest'],p)
    for artifact in previous['artifact_manifest']:
        source=source_directory/artifact['file'];target=directory/artifact['file']
        try:os.link(source,target)
        except OSError:shutil.copy2(source,target)
    for name in ('source-inputs.tar.gz','execution-manifest.json'):
        if (source_directory/name).exists():shutil.copy2(source_directory/name,directory/name)
    state=deepcopy(previous)
    state.update(id=uuid.uuid4().hex,status='running',error=None,failed_runs=[],statistics=[],
        started_at=time.time(),finished_at=None,incomplete_traces=[],storage=storage,
        prior_interruptions=[*previous.get('prior_interruptions',[]),dict(id=previous['id'],status=previous['status'],error=previous.get('error'),
            failed_runs=previous.get('failed_runs',[]),incomplete_traces=previous.get('incomplete_traces',[]))],
        resumed_from=dict(directory=str(source_directory.resolve()),state_sha256=hashlib.sha256((source_directory/'state.json').read_bytes()).hexdigest(),
            retained_completed_runs=len(retained),missing_runs=len(missing),source_sha256=source_fingerprint()))
    filename=directory/'state.json';_atomic_json(filename,state)
    try:
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            futures={pool.submit(_parallel_run,(state['fleet_records'],state['simulation_config'],s,a,seed,str(directory))):(s,seed,a) for s,seed,a in missing}
            try:
                for future in as_completed(futures):
                    scenario,seed,arm=futures[future]
                    state.update(current_scenario=scenario,current_seed=seed,current_policy=arm)
                    done={(r['scenario'],r['seed'],r['policy_id']) for r in state['results']}
                    remaining=[job for job in missing if job not in done]
                    state['storage']=projected_storage(directory,remaining,state['results'],state['artifact_manifest'],p)
                    row=future.result()
                    if row is None:raise RuntimeError('Continuation interrupted; completed observations retained')
                    state['results'].append(row);state['trace_files'].append(row['trace_file'])
                    trace=directory/row['trace_file']
                    with trace.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
                    state['artifact_manifest'].append(dict(file=trace.name,bytes=trace.stat().st_size,sha256=checksum));sizes.append(trace.stat().st_size)
                    state['progress']=len(state['results'])/p['total_runs'];_atomic_json(filename,state)
                state.update(status='completed',progress=1.)
            except BaseException:
                (directory/'cancel.requested').touch()
                for f in futures:f.cancel()
                raise
    except (Exception,KeyboardInterrupt) as exc:
        state.update(status='failed',error=str(exc) or 'Interrupted by operator')
        state['failed_runs'].append(dict(scenario=state.get('current_scenario'),seed=state.get('current_seed'),policy=state.get('current_policy'),error=state['error']))
    finally:
        state['results'].sort(key=lambda r:(SCENARIOS.index(r['scenario']),r['seed'],ARMS.index(r['policy_id'])))
        state['results_sha256']=digest(scientific_rows(state['results']));state['finished_at']=time.time()
        state['statistics']=statistical_report(state['results'],SCENARIOS,TOPOLOGIES)
        state['incomplete_traces']=[f.name for f in directory.glob('*.jsonl.gz') if f.name not in state['trace_files']]
        _atomic_json(filename,state)
        for fmt in ('json','csv','md','manifest'):
            content=export_study(state,fmt)
            if fmt=='md':content+='\n## Prior interruptions\n\n'+'\n'.join('- '+str(event['error']) for event in state['prior_interruptions'])+'\n\nCompleted observations were hash-validated and retained without changing the numerical source. Only missing runs were executed in this continuation.\n'
            (directory/f'results.{fmt}').write_text(content)
    print(json.dumps(dict(status=state['status'],runs=len(state['results']),source_sha256=state['source_sha256']),indent=2))
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('directory',type=Path)
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=2);args=parser.parse_args()
    state=resume(args.source,args.directory,args.workers)
    if state['status']!='completed':raise SystemExit(1)
