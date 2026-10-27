"""Independent conservation, paired-input, liveness and safety trace reduction."""
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import json
import time
from pathlib import Path


def verify_row(directory,row):
    path=directory/row['trace_file']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=row['trace_sha256']:raise ValueError('Trace hash mismatch')
    final_ids=set();counts=Counter();safety=Counter();deliveries=[];receipts=[];last_packet=0;manifest=None;measured_safety=[]
    with gzip.open(path,'rt') as stream:
        for line in stream:
            r=json.loads(line);kind=r['kind'];d=r.get('data',{});at=r.get('simulation_time',0)
            if kind=='manifest':manifest=r;continue
            if kind=='packet_result':
                identity=d['id'];last_packet=max(last_packet,int(identity[1:]));
                if identity in final_ids:raise ValueError('Duplicate packet finalization')
                final_ids.add(identity);counts[d['status']]+=1
                if d['status']=='delivered':
                    if d['destination']=='base':deliveries.append((d['completed_at'],d['source']))
                    key=d.get('channel_key','').split(':')
                    if len(key)>=7 and key[:2]==['autonomy','response'] and key[2]=='base' and key[4]=='False':
                        if d['completed_at']>=float(key[5])+2.9+1e-6:raise ValueError('Expired liveness receipt was delivered')
                        receipts.append((d['completed_at'],d['destination']))
            if at<180 and kind in ('safety_latched','landing_completed','unsafe_ground_contact','battery_exhausted','geofence_breaches','separation_violations'):
                safety[kind]+=1;measured_safety.append(r)
    if not manifest or manifest['shared_input_sha256']!=row['shared_input_sha256']:raise ValueError('Manifest mismatch')
    if len(final_ids)!=row['generated'] or last_packet!=row['generated'] or counts['delivered']!=row['delivered'] or counts['dropped']!=row['dropped']:raise ValueError('Conservation mismatch')
    names=[f'sim_uav_{n:03d}' for n in range(1,31)]
    def coverage(events):
        events.sort();i=0;latest={};samples=[]
        for step in range(0,3600,4):
            now=(step+1)*.05
            while i<len(events) and events[i][0]<=now+1e-8:
                at,node=events[i];latest[node]=at;i+=1
            samples.append(sum(now-latest.get(n,-100)<3-1e-9 for n in names)/30)
        return sum(samples)/len(samples)
    delivered_coverage=coverage(deliveries)
    if abs(delivered_coverage-row['delivered_network_availability'])>1e-8:raise ValueError('Delivered availability mismatch')
    verified=coverage(receipts) if row['autonomy']=='on' else None
    if verified is not None and abs(verified-row['verified_roundtrip_availability'])>1e-8:raise ValueError('Roundtrip availability mismatch')
    for field,event in [('interventions','safety_latched'),('landings','landing_completed'),('unsafe_contacts','unsafe_ground_contact'),('battery_exhaustions','battery_exhausted'),('geofence_breaches','geofence_breaches'),('separation_violations','separation_violations')]:
        if row['safety_counters'].get(field,0)!=safety[event]:raise ValueError('Safety counter mismatch '+field)
    return dict(scenario=row['scenario'],autonomy=row['autonomy'],seed=row['seed'],outcome='Passed',packets=len(final_ids),delivered_availability=delivered_coverage,verified_availability=verified,safety_events=len(measured_safety))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--watch',action='store_true');args=p.parse_args()
    checks={};row_hashes={}
    while True:
        state=json.loads((args.directory/'state.json').read_text())
        for row in state['results']:
            identity=(row['scenario'],row['autonomy'],row['seed'])
            if identity in checks:continue
            checks[identity]=verify_row(args.directory,row)
            row_hashes[identity]=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
            print('verified',len(checks),*identity,flush=True)
        if state['status']!='running' or not args.watch:break
        (args.directory/'verification-progress.json').write_text(json.dumps(dict(outcome='Not Tested',note='Batch incomplete; only the listed completed runs have been independently reduced.',completed_checks=list(checks.values())),indent=2))
        time.sleep(2)
    if state['status']!='completed' or len(state['results'])!=60:raise ValueError('Incomplete batch; no passing release reduction')
    groups=defaultdict(list)
    for row in state['results']:
        identity=(row['scenario'],row['autonomy'],row['seed'])
        if row_hashes[identity]!=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest():raise ValueError('Result changed after reduction')
        if hashlib.sha256((args.directory/row['trace_file']).read_bytes()).hexdigest()!=row['trace_sha256']:raise ValueError('Trace changed after reduction')
        groups[row['scenario'],row['seed']].append(row)
    if len(groups)!=30 or any(len(g)!=2 or len({r['shared_input_sha256'] for r in g})!=1 for g in groups.values()):raise ValueError('Paired inputs differ')
    values=sorted(checks.values(),key=lambda c:(c['scenario'],c['seed'],c['autonomy']))
    result=dict(outcome='Passed',source_sha256=state['source_sha256'],runs=len(values),paired_groups=len(groups),packet_results=sum(c['packets'] for c in values),checks=values)
    (args.directory/'independent-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='checks'},indent=2))

if __name__=='__main__':main()
