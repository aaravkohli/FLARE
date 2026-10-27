"""Independently check a retained study, input matching and streamed packet traces."""
from __future__ import annotations
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path


def verify(directory:Path):
    state=json.loads((directory/'state.json').read_text());p=state['protocol'];rows=state['results'];checks=[]
    def check(name,ok,detail=None):
        checks.append(dict(check=name,outcome='Passed' if ok else 'Failed',detail=detail))
    expected={(s,seed,a) for s in p['scenarios'] for seed in p['seeds'] for a in p['policies']}
    actual=[(r['scenario'],r['seed'],r['policy_id']) for r in rows]
    check('Complete unique protocol run set',state['status']=='completed' and len(actual)==len(expected) and set(actual)==expected,
        dict(status=state['status'],completed=len(rows),expected=len(expected),failed_runs=state.get('failed_runs',[])))
    groups=defaultdict(list)
    for r in rows:groups[r['scenario'],r['seed']].append(r)
    for name in ('fleet_sha256','initial_input_sha256','motion_sha256','offered_input_sha256','shared_input_sha256'):
        check('Matched '+name,all(len({r[name] for r in group})==1 for group in groups.values()))
    check('One compatible design',len({r['design_sha256'] for r in rows})==1)
    check('Packet conservation and TTL drain',all(r['conservation'] and r['packet_accounting']['pending']==0 and
        r['packet_accounting']['generated']==r['packet_accounting']['delivered']+r['packet_accounting']['dropped'] for r in rows))
    check('Primary cohort size and accounting',all(r['generated']==1800 and r['generated']==r['delivered']+r['dropped'] for r in rows))
    check('All offered packets accounted',all(r['packet_accounting']['generated']==1950+(8500 if r['scenario']=='congestion' else 0) for r in rows))
    congestion=[r for r in rows if r['scenario']=='congestion']
    check('Real congestion hop transmissions',bool(congestion) and all(r['background_offered']==8500 and r['background_transmissions']>0 for r in congestion))
    check('Bottleneck queue pressure',any(r['policy_id']=='star' and r['queue_peak']>=64 and r['queue_overflows']>0 for r in congestion))
    check('Unrecovered flows retained',all(r['unrecovered_flows']==sum(f.get('service_recovered_at') is None for f in r['recovery']) for r in rows))
    scientific=[{k:v for k,v in r.items() if k!='timing'} for r in rows]
    result_hash=hashlib.sha256(json.dumps(scientific,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    check('Scientific result hash excludes timings',result_hash==state['results_sha256'])
    bad_hashes=[]
    for artifact in state['artifact_manifest']:
        path=directory/artifact['file']
        if not path.is_file():bad_hashes.append(path.name);continue
        with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        if checksum!=artifact['sha256'] or path.stat().st_size!=artifact['bytes']:bad_hashes.append(path.name)
    check('Every compressed artifact hash',len(state['artifact_manifest'])==len(rows) and not bad_hashes,bad_hashes)
    # Predeclared stratified trace sample: every arm/case at the first preset seed.
    trace_checks=[]
    for r in rows:
        if r['seed']!=p['seeds'][0]:continue
        outcomes=hashlib.sha256();primary=[];background=set();last=-1.;ordered=True;max_time=0.
        with gzip.open(directory/r['trace_file'],'rt') as stream:
            for line in stream:
                event=json.loads(line);kind=event['kind']
                if 'simulation_time' in event:
                    at=event['simulation_time'];max_time=max(max_time,at)
                    if kind.startswith('packet_'):ordered &= at>=last-1e-8;last=at
                if not kind.startswith('packet_'):continue
                canonical=dict(kind=kind,simulation_time=event['simulation_time'],data=event['data'])
                outcomes.update(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()+b'\n')
                data=event['data']
                if kind=='packet_queued' and data['correlation_id']=='reference_background':background.add((data['source'],data['destination']))
                if kind=='packet_result' and data['correlation_id']=='reference' and 5<=data['created_at']<65:primary.append(data)
        delivered=[d for d in primary if d['status']=='delivered'];latencies=[(d['completed_at']-d['created_at'])*1000 for d in delivered]
        close=lambda a,b: a is None and b is None or a is not None and b is not None and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8)
        okay=(len(primary)==r['generated'] and len(delivered)==r['delivered'] and
            close(len(delivered)/len(primary),r['pdr']) and
            close(sum(latencies)/len(latencies) if latencies else None,r['average_latency_ms']) and
            close(sum(d['size_bytes'] for d in delivered)*8/60/1e6,r['throughput_mbps']) and
            outcomes.hexdigest()==r['outcome_sha256'] and ordered and max_time<=85+1e-8 and
            (r['scenario']!='congestion' or len(background)==5 and all(b!='base' for _,b in background)))
        trace_checks.append(dict(scenario=r['scenario'],policy=r['policy_id'],seed=r['seed'],outcome='Passed' if okay else 'Failed',packet_events_ordered=ordered,maximum_simulation_time=max_time))
    check('Stratified independent trace/cohort verification',len(trace_checks)==len(p['scenarios'])*len(p['policies']) and all(r['outcome']=='Passed' for r in trace_checks),trace_checks)
    return dict(source_sha256=state['source_sha256'],results_sha256=state['results_sha256'],checks=checks,
        outcome='Passed' if all(c['outcome']=='Passed' for c in checks) else 'Failed',
        trace_sample='All cases and policies at first declared seed; all compressed artifacts separately hash-checked')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);args=parser.parse_args()
    report=verify(args.directory);(args.directory/'verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='checks'},indent=2))
    if report['outcome']!='Passed':raise SystemExit(1)
