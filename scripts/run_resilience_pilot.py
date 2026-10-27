"""Execute only the isolated exploratory resilience pilot, never live services."""
import argparse
import json
from pathlib import Path
from queue import Queue
import uuid
import hashlib
import tarfile
import time
import multiprocessing
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor,as_completed
import yaml
from simulation.swarm.resilience_study import study_worker


def parallel_pilot(config,directory,workers):
    from scripts.resume_resilience_pilot import execute
    from simulation.swarm.resilience_study import protocol,summaries,export_study
    from simulation.swarm.adaptive_study import digest,storage_preflight
    from simulation.swarm.experiments import source_fingerprint,_atomic_json,SIMULATOR_VERSION
    design=protocol(config['packets']['ttl_s']);storage_preflight(directory,72)
    state=dict(id=uuid.uuid4().hex,mode='adaptive_study',configuration=dict(mode='adaptive_study',protocol_id=design['id'],preset='pilot'),
        protocol=design,status='running',progress=0.,source_sha256=source_fingerprint(),simulator_version=SIMULATOR_VERSION,
        simulation_config=deepcopy(config),fleet_records=[],results=[],statistics=[],trace_files=[],artifact_manifest=[],failed_runs=[],
        evidence='packet_simulation',started_at=time.time(),offline_workers=workers)
    _atomic_json(directory/'state.json',state)
    try:
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            futures={pool.submit(execute,([],config,s,a,n,str(directory))):(s,n,a) for s in design['scenarios'] for n in design['seeds'] for a in design['policies']}
            try:
                for future in as_completed(futures):
                    scenario,seed,arm=futures[future];state.update(current_scenario=scenario,current_seed=seed,current_policy=arm)
                    row=future.result()
                    if row is None:raise RuntimeError('Pilot interrupted')
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
        for fmt in ('json','csv','md','manifest'):(directory/f'results.{fmt}').write_text(export_study(state,fmt))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--workers',type=int,choices=(1,2),default=1,help='Independent offline workers; API jobs remain single-worker')
    args=parser.parse_args();args.directory.mkdir(parents=True,exist_ok=False)
    config=yaml.safe_load((Path(__file__).resolve().parents[1]/'config/swarm_config.yaml').read_text())
    from simulation.swarm.experiments import source_fingerprint
    files=[*Path('simulation/swarm').glob('*.py'),Path('schemas/mission.py'),Path('config/swarm_config.yaml'),Path('frontend-react/src/mission-simulation/assets/terrain/heightmap.ts')]
    with tarfile.open(args.directory/'execution-source.tar.gz','w:gz') as archive:
        for path in files:archive.add(path,arcname=path)
    (args.directory/'execution-source.json').write_text(json.dumps(dict(source_sha256=source_fingerprint(),files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}),indent=2))
    if args.workers==1:study_worker(uuid.uuid4().hex,dict(mode='adaptive_study',protocol_id='evidence_resilience_pilot_v1',preset='pilot'),[],config,args.directory,Queue())
    else:parallel_pilot(config,args.directory,args.workers)
    state=json.loads((args.directory/'state.json').read_text());print(json.dumps({k:state.get(k) for k in ('status','progress','error','source_sha256','results_sha256')},indent=2))
    if state['status']!='completed':raise SystemExit(1)

if __name__=='__main__':main()
