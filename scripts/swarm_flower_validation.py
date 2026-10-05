"""Exercise actual Flower training, poisoning analysis, and packet delivery gates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import requests
from provenance.artifacts import write_experiment_run


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--api',default='http://127.0.0.1:18000');parser.add_argument('--directory',type=Path,default=Path('/private/tmp/flare-swarm-final'));args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    auth=requests.post(args.api+'/auth/token',data={'username':os.getenv('AJ_ADMIN_USERNAME','admin'),'password':os.getenv('AJ_ADMIN_PASSWORD','antijam2026')},timeout=5);auth.raise_for_status()
    headers={'Authorization':'Bearer '+auth.json()['access_token']}
    def call(method,path='',body=None):
        response=requests.request(method,args.api+'/mission/simulation'+path,headers=headers,json=body,timeout=10);response.raise_for_status();return response.json()
    state=call('GET');state=call('POST','/scenarios/poisoning',{'action':'reset','expected_revision':state['revision']})
    # Explicit safe satellite preferences prevent the mesh bottleneck from
    # obscuring update-security validation. They remain safety-constrained.
    call('POST','/commands',{'command_id':'validation-satellite','session_id':state['session_id'],'expected_revision':state['revision'],'targets':[n['id'] for n in state['nodes']],'action':'route','parameters':{'family':'satellite'},'ttl_s':60})
    state=call('POST','/control',{'action':'play','expected_revision':state['revision']})
    cursor=0;events=[];pause_verified=False;failed_identity=None;deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        state=call('GET',f'?after={cursor}');page=call('GET',f'/events?after={cursor}');fresh=page['events'];cursor=page['cursor']
        while cursor<page['latest_cursor']:
            page=call('GET',f'/events?after={cursor}');fresh.extend(page['events']);cursor=page['cursor']
        events.extend(fresh);state['events']=fresh
        started=[e for e in state['events'] if e['kind']=='upload_started']
        if started: print(json.dumps({'time':state['simulation_time'],'uploads':[e['data']['client_id'] for e in started],'nodes':[(n['id'],n['selected_route'],n['containment'],n.get('security',{}).get('operator_preference_applied') if n.get('security') else None) for n in state['nodes'] if n['id'] in [e['data']['client_id'] for e in started]]}),flush=True)
        if started and not pause_verified:
            paused=call('POST','/control',{'action':'pause','expected_revision':state['revision']});time.sleep(.6);still=call('GET')
            pause_verified=paused['simulation_time']==still['simulation_time'] and paused['statistics']['delivered']==still['statistics']['delivered']
            state=call('POST','/control',{'action':'play','expected_revision':still['revision']})
        if failed_identity is None and started:
            plan=json.loads((args.directory/'pool.json').read_text());first=plan['clients'][0]
            candidate=next((e['data']['client_id'] for e in started if e['data']['client_id']!=first),None)
            if candidate:
                failed_identity=candidate
                call('POST','/commands',{'command_id':'validation-transport-failure','session_id':state['session_id'],'expected_revision':state['revision'],'targets':[candidate],'action':'disconnect'})
        aggregated=[e for e in events if e['kind']=='aggregation_result' and e['data'].get('security_audits')]
        for event in state['events']:
            if event['kind']=='aggregation_result':print(json.dumps(event['data']),flush=True)
        if aggregated and max(e['data'].get('round',0) for e in aggregated)>=3:break
        time.sleep(.05)
    state=call('GET');state=call('POST','/control',{'action':'pause','expected_revision':state['revision']})
    outcomes=[e['data'] for e in events if e['kind']=='aggregation_result']
    deliveries=[e['data'] for e in events if e['kind']=='upload_completed']
    security=[r for event in outcomes for r in event.get('security_audits',[])];actions={r['client_id']:r.get('action') for r in security}
    armed=[e['data'] for e in events if e['kind']=='poisoning_armed']
    poisoning_observed=bool(armed and any(r['client_id']==armed[0]['client_id'] and r.get('action') in ('REJECTED','DOWN_WEIGHTED') for r in security))
    complete=pause_verified and any(d['status']=='delivered' for d in deliveries) and any(d['status']=='delivery_failed' for d in deliveries) and any(e.get('delivery_failures') or e.get('failed_deliveries') for e in outcomes) and poisoning_observed
    payload='\n'.join(json.dumps(e,sort_keys=True) for e in events)
    report={'actual_flower':True,'session_id':state['session_id'],'pool_size':5,'pause_verified':pause_verified,'forced_failed_identity':failed_identity,'deliveries':deliveries,'aggregation_results':outcomes,'security_actions':actions,'poisoning_armed':armed,'poisoning_observed':poisoning_observed,'complete':bool(complete),'evidence':'controlled_simulation','limitations':['Synthetic training data; modeled packet transport; no real wireless/OpenFlow claim']}
    files={'events.jsonl':payload}
    for name in ('flower.log','clients.log','fl_metrics.json','pool.json'):
        path=args.directory/name
        if path.exists():files[name]=path.read_text()
    run=write_experiment_run(base=root,experiment='swarm_flower_delivery',protocol_version='swarm_flower_v1',report=report,seeds=[42],evidence_category='controlled_simulation',config_paths=[root/'config/swarm_config.yaml',root/'config/fl_config.yaml'],checkpoint_paths=[root/'models/fl_model.pth',root/'models/rl_model_v3.zip'],dataset_fingerprints={'actual_flower_events':hashlib.sha256(payload.encode()).hexdigest(),**{identity:digest for event in outcomes for identity,digest in event.get('datasets',{}).items() if digest}},command=[sys.executable,'-m','scripts.swarm_flower_validation',*sys.argv[1:]],status='passed' if complete else 'incomplete',extra_files=files)
    print(json.dumps({'report':str(run.report_path),'complete':bool(complete),'pause_verified':pause_verified,'deliveries':len(deliveries),'actions':actions,'poisoning_observed':poisoning_observed}),flush=True)

if __name__=='__main__':main()
