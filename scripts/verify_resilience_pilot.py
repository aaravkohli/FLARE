"""Independently reduce immutable raw pilot traces into a corrected release copy."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
from simulation.swarm.adaptive_study import digest
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.resilience_study import summaries,export_study,ARMS,SCENARIOS,SEEDS
from simulation.swarm.resilience_analysis import verify_trace


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True,type=Path);parser.add_argument('--directory',required=True,type=Path)
    args=parser.parse_args();raw=json.loads((args.input/'state.json').read_text());expected={(s,a,n) for s in SCENARIOS for a in ARMS for n in SEEDS}
    if raw['status']!='completed' or {(r['scenario'],r['policy_id'],r['seed']) for r in raw['results']}!=expected:raise ValueError('Incomplete or incompatible pilot')
    args.directory.mkdir(parents=True,exist_ok=False);state=deepcopy(raw);checks=[];corrections=[];groups={}
    for row in state['results']:
        path=args.input/row['trace_file'];manifest=next(a for a in raw['artifact_manifest'] if a['file']==path.name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('Trace hash mismatch')
        evidence=verify_trace(path,row);checks.append(dict(scenario=row['scenario'],policy=row['policy_id'],seed=row['seed'],**evidence))
        for field in ('evidence_recovery_ms','evidence_unrecovered','deferred_packets','deferred_dropped'):
            if row[field]!=evidence[field]:corrections.append(dict(scenario=row['scenario'],policy=row['policy_id'],seed=row['seed'],field=field,before=row[field],after=evidence[field]))
            row[field]=evidence[field]
        groups.setdefault((row['scenario'],row['seed']),[]).append(row)
        shutil.copy2(path,args.directory/path.name)
    for key,rows in groups.items():
        if len(rows)!=4 or len({r['shared_input_sha256'] for r in rows})!=1:raise ValueError(f'Unmatched external inputs: {key}')
        if len({r['report_schedule_sha256'] for r in rows})!=1:raise ValueError('Unmatched report capture schedule')
    state['analysis_source_sha256']=source_fingerprint()
    state['execution_source_sha256']=raw['source_sha256'] # Never relabel actual execution.
    state['measurement_corrections']=corrections;state['statistics']=summaries(state['results'])
    state['results_sha256']=digest([{k:v for k,v in r.items() if k!='timing'} for r in state['results']])
    state['analysis']='Receipt reconstruction: no recovery anchor in normal; loss must precede condition end; deferred counters from events. Packet outcomes untouched.'
    for name in ('execution-source.tar.gz','execution-source.json'):shutil.copy2(args.input/name,args.directory/name)
    files=[*Path('simulation/swarm').glob('*.py'),Path('schemas/mission.py'),Path('config/swarm_config.yaml'),Path('frontend-react/src/mission-simulation/assets/terrain/heightmap.ts')]
    with tarfile.open(args.directory/'analysis-source.tar.gz','w:gz') as tar:
        for p in files:tar.add(p,arcname=p)
    verification=dict(status='Passed',runs=len(checks),input_groups=len(groups),primary_packets=sum(r['primary_packets'] for r in checks),packet_results=sum(r['packet_results'] for r in checks),
        report_receipts=sum(r['report_receipts'] for r in checks),corrections=corrections,checks=checks)
    (args.directory/'independent-verification.json').write_text(json.dumps(verification,indent=2))
    (args.directory/'state.json').write_text(json.dumps(state,allow_nan=False))
    for fmt in ('json','csv','md','manifest'):
        content=export_study(state,fmt)
        if fmt=='md' and state.get('prior_interruptions'):
            content+='\n## Retained interruptions\n\n'+'\n'.join('- '+str(event.get('error')) for event in state['prior_interruptions'])+'\n'
        (args.directory/f'results.{fmt}').write_text(content)
    print(json.dumps({k:verification[k] for k in ('status','runs','input_groups','primary_packets','packet_results','report_receipts')},indent=2))
    print('Measurement corrections',len(corrections))

if __name__=='__main__':main()
