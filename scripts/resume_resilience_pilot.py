"""Continue an interrupted exploratory pilot without rewriting retained evidence.

This is offline artifact recovery, not a new API or a study pause/resume control.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import time
import uuid
from simulation.swarm.adaptive_study import digest,storage_preflight
from simulation.swarm.experiments import _atomic_json,source_fingerprint
from simulation.swarm.resilience_study import protocol,run_one,summaries,export_study


def validate(previous,source):
    if previous.get('status') not in ('failed','cancelled'):raise ValueError('Pilot must be terminal and interrupted')
    if previous.get('source_sha256')!=source_fingerprint():raise ValueError('Source changed; cannot combine runs')
    design=protocol(previous['simulation_config']['packets']['ttl_s'])
    if previous['protocol']!=design:raise ValueError('Protocol changed; cannot combine runs')
    expected={(s,n,a) for s in design['scenarios'] for n in design['seeds'] for a in design['policies']}
    retained={(r['scenario'],r['seed'],r['policy_id']) for r in previous['results']}
    if len(retained)!=len(previous['results']) or not retained<=expected:raise ValueError('Invalid retained identities')
    artifacts={a['file']:a for a in previous['artifact_manifest']}
    for row in previous['results']:
        name=row['trace_file']
        if Path(name).name!=name or name not in artifacts:raise ValueError('Invalid retained trace')
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=artifacts[name]['sha256']:raise ValueError('Trace hash mismatch')
    return design,retained


def execute(task):
    records,config,scenario,arm,seed,directory=task
    return run_one(records,config,scenario,arm,seed,directory,cancel=lambda:(Path(directory)/'cancel.requested').exists())


def resume(source,directory,workers=1):
    previous=json.loads((source/'state.json').read_text());design,retained=validate(previous,source)
    missing=[(s,n,a) for s in design['scenarios'] for n in design['seeds'] for a in design['policies'] if (s,n,a) not in retained]
    directory.mkdir(parents=True,exist_ok=False)
    storage_preflight(directory,len(missing),[a['bytes'] for a in previous['artifact_manifest']])
    for artifact in previous['artifact_manifest']:shutil.copy2(source/artifact['file'],directory/artifact['file'])
    for name in ('execution-source.tar.gz','execution-source.json'):shutil.copy2(source/name,directory/name)
    state=deepcopy(previous)
    state.update(id=uuid.uuid4().hex,status='running',error=None,failed_runs=[],statistics=[],started_at=time.time(),finished_at=None,
        prior_interruptions=[*previous.get('prior_interruptions',[]),{k:previous.get(k) for k in ('id','status','error','failed_runs','incomplete_traces')}],
        resumed_from=dict(directory=str(source.resolve()),state_sha256=hashlib.sha256((source/'state.json').read_bytes()).hexdigest(),retained_runs=len(retained),missing_runs=len(missing),workers=workers))
    _atomic_json(directory/'state.json',state)
    try:
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            futures={pool.submit(execute,(state['fleet_records'],state['simulation_config'],s,a,n,str(directory))):(s,n,a) for s,n,a in missing}
            try:
                for future in as_completed(futures):
                    scenario,seed,arm=futures[future];state.update(current_scenario=scenario,current_seed=seed,current_policy=arm)
                    row=future.result()
                    if row is None:raise RuntimeError('Continuation interrupted')
                    state['results'].append(row);state['trace_files'].append(row['trace_file']);trace=directory/row['trace_file']
                    state['artifact_manifest'].append(dict(file=trace.name,bytes=trace.stat().st_size,sha256=hashlib.sha256(trace.read_bytes()).hexdigest()))
                    state['progress']=len(state['results'])/72;_atomic_json(directory/'state.json',state)
                state.update(status='completed',progress=1.)
            except BaseException:
                (directory/'cancel.requested').touch()
                for future in futures:future.cancel()
                raise
    except (Exception,KeyboardInterrupt) as error:
        state.update(status='failed',error=str(error) or 'Interrupted')
        state['failed_runs'].append(dict(scenario=state.get('current_scenario'),seed=state.get('current_seed'),policy=state.get('current_policy'),error=state['error']))
    finally:
        state['results'].sort(key=lambda r:(design['scenarios'].index(r['scenario']),r['seed'],design['policies'].index(r['policy_id'])))
        state['results_sha256']=digest([{k:v for k,v in row.items() if k!='timing'} for row in state['results']])
        state['statistics']=summaries(state['results']);state['finished_at']=time.time()
        state['incomplete_traces']=[p.name for p in directory.glob('*.jsonl.gz') if p.name not in state['trace_files']]
        _atomic_json(directory/'state.json',state)
        for fmt in ('json','csv','md','manifest'):
            content=export_study(state,fmt)
            if fmt=='md':content+='\n## Retained interruptions\n\n'+'\n'.join('- '+str(event.get('error')) for event in state['prior_interruptions'])+'\n\nOnly missing runs were executed; completed traces were hash-validated against the same source and protocol.\n'
            (directory/f'results.{fmt}').write_text(content)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('directory',type=Path)
    parser.add_argument('--workers',type=int,choices=(1,2),default=1);args=parser.parse_args()
    result=resume(args.source,args.directory,args.workers);print(json.dumps(dict(status=result['status'],runs=len(result['results']),error=result.get('error')),indent=2))
    if result['status']!='completed':raise SystemExit(1)
