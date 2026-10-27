"""Derive stable recovery-record ordering without altering any measurement.

The raw completed execution remains immutable and source-bound. The derived
manifest records both its execution source and the serialization repair source.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from simulation.swarm.adaptive_study import digest, scientific_rows, export_study
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.study_statistics import statistical_report
from schemas.mission import TOPOLOGIES


def canonicalize(source,directory):
    raw=json.loads((source/'state.json').read_text())
    if raw['status']!='completed' or len(raw['results'])!=raw['protocol']['total_runs']:
        raise ValueError('Canonical export requires a complete study')
    state=deepcopy(raw)
    changes=[]
    for row,original in zip(state['results'],raw['results']):
        row['recovery']=sorted(row['recovery'],key=lambda r:(r.get('source',''),r.get('destination','')))
        for metric in ('route_convergence_ms','service_recovery_ms'):
            values=[r[metric] for r in row['recovery'] if r.get(metric) is not None]
            value=sum(values)/len(values) if values else None
            if value!=row[metric]:
                difference=abs(value-row[metric]);assert difference<1e-8
                changes.append(dict(scenario=row['scenario'],policy=row['policy_id'],seed=row['seed'],metric=metric,delta_ms=difference))
            row[metric]=value
        assert {k:v for k,v in row.items() if k not in ('recovery','route_convergence_ms','service_recovery_ms')}=={k:v for k,v in original.items() if k not in ('recovery','route_convergence_ms','service_recovery_ms')}
        assert sorted(row['recovery'],key=lambda r:json.dumps(r,sort_keys=True))==sorted(original['recovery'],key=lambda r:json.dumps(r,sort_keys=True))
    state.update(raw_results_sha256=raw['results_sha256'],results_sha256=digest(scientific_rows(state['results'])),
        result_serialization='stable_recovery_order_and_accumulation_v1',serialization_source_sha256=source_fingerprint(),
        recovery_accumulation_changes=changes,
        raw_execution=dict(directory=str(source.resolve()),state_sha256=hashlib.sha256((source/'state.json').read_bytes()).hexdigest(),source_sha256=raw['source_sha256']))
    directory.mkdir(parents=True,exist_ok=False)
    state['statistics']=statistical_report(state['results'],state['protocol']['scenarios'],TOPOLOGIES)
    assert [r for r in state['statistics'] if r['metric'] not in ('route_convergence_ms','service_recovery_ms')]==[r for r in raw['statistics'] if r['metric'] not in ('route_convergence_ms','service_recovery_ms')]
    for name in raw['trace_files']:
        try:os.link(source/name,directory/name)
        except OSError:shutil.copy2(source/name,directory/name)
    for name in ('source-inputs.tar.gz','execution-manifest.json','continuation-manifest.json'):
        if (source/name).exists():shutil.copy2(source/name,directory/name)
    shutil.copy2(Path('simulation/swarm/experiments.py'),directory/'serialization-source.py')
    (directory/'state.json').write_text(json.dumps(state,allow_nan=False))
    for fmt in ('json','csv','md','manifest'):
        content=export_study(state,fmt)
        if fmt=='md':
            content+='\n## Execution and serialization provenance\n\nThe completed raw study remains unchanged. Recovery records and their averaging order were canonicalized for deterministic exports. Packet measurements, all delivery comparisons, and metric definitions are unchanged. Recovery-average floating point changes: '+str(len(changes))+' (maximum '+str(max((c['delta_ms'] for c in changes),default=0))+' ms). Execution source: `'+raw['source_sha256']+'`. Serialization repair source: `'+state['serialization_source_sha256']+'`.\n'
            content+='\n## Prior resource interruptions\n\n'+'\n'.join('- '+str(event['error']) for event in state.get('prior_interruptions',[]))+'\n'
        (directory/f'results.{fmt}').write_text(content)
    print(json.dumps(dict(status=state['status'],runs=len(state['results']),packet_measurements_unchanged=True,recovery_accumulation_changes=len(changes),maximum_delta_ms=max((c['delta_ms'] for c in changes),default=0),execution_source=state['source_sha256'],serialization_source=state['serialization_source_sha256']),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('directory',type=Path);args=parser.parse_args()
    canonicalize(args.source,args.directory)
