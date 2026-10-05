"""Record seeded jamming sequences through actual authenticated service boundaries."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import requests
from provenance.artifacts import write_experiment_run


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--api',default='http://127.0.0.1:18000');parser.add_argument('--seconds',type=float,default=30);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    auth=requests.post(args.api+'/auth/token',data={'username':os.getenv('AJ_ADMIN_USERNAME','admin'),'password':os.getenv('AJ_ADMIN_PASSWORD','antijam2026')},timeout=5);auth.raise_for_status()
    headers={'Authorization':'Bearer '+auth.json()['access_token']}
    def call(method,path,body=None):
        response=requests.request(method,args.api+'/mission/simulation'+path,headers=headers,json=body,timeout=10);response.raise_for_status();return response.json()
    sequences=[];traces=[]
    for seed in (7,42,99):
        state=call('GET','');state=call('PATCH','/config',{'seed':seed,'speed':4,'expected_revision':state['revision']})
        state=call('POST','/scenarios/jamming',{'action':'reset','expected_revision':state['revision']})
        state=call('POST','/control',{'action':'play','expected_revision':state['revision']})
        events=[];cursor=0;baseline=state;deadline=time.monotonic()+120
        while state['simulation_time']<args.seconds:
            time.sleep(.2);state=call('GET',f'?after={cursor}');page=call('GET',f'/events?after={cursor}'); fresh=page['events']; cursor=page['cursor']
            while cursor<page['latest_cursor']:
                page=call('GET',f'/events?after={cursor}');fresh.extend(page['events']);cursor=page['cursor']
            events.extend(fresh);state['events']=fresh
            if state['simulation_time']<8:baseline=state
            traces.append({'seed':seed,'simulation_time':state['simulation_time'],'statistics':state['statistics'],'nodes':state['nodes'],'events':state['events']})
            if time.monotonic()>deadline:raise RuntimeError('Live simulation did not reach the requested duration')
        state=call('POST','/control',{'action':'pause','expected_revision':state['revision']})
        after_attack=[e for e in events if e['simulation_time']>=8]
        baseline_scores={n['id']:(n.get('security') or {}).get('scores',[0,0,0]) for n in baseline['nodes']}
        detections=[e for e in after_attack if e['kind']=='detector_observation' and (
            (e['data'].get('network_detection',{}).get('risk_score') or 0)>.35 or
            any(score>.8 and score-old>.35 for score,old in zip(e['data'].get('scores',[]),baseline_scores.get(e['data'].get('node'),[0,0,0]))))]
        detected_decisions={d['data']['decision_id'] for d in detections}
        operations=Counter(e['kind'] for e in after_attack if e['category']=='SDN')
        prior={f['policy_id']:f['hops'] for f in baseline['flows']}
        changed=[e for e in after_attack if e['kind']=='INSTALL' and e['data'].get('hops') and e['data']['hops']!=prior.get(e['data']['policy_id']) and e['data'].get('decision_id') in detected_decisions]
        matches=[]
        for installed in changed:
            flow=installed['data']
            packet=next((p for p in after_attack if p['kind']=='packet_hop_completed' and p['simulation_time']>=installed['simulation_time'] and p['data']['created_at']>=installed['simulation_time'] and p['data']['route']==flow['hops'] and p['data']['category']==flow['traffic_class']),None)
            if packet: matches.append({'decision_id':flow['decision_id'],'policy_id':flow['policy_id'],'installed_cursor':installed['cursor'],'packet_cursor':packet['cursor'],'packet_id':packet['data']['id'],'hops':flow['hops']})
        proposals=[e for e in after_attack if e['kind']=='route_proposed']
        relevant_proposals=[p for p in proposals if any(p['data'].get('decision_id')==d['data'].get('decision_id') for d in detections)]
        sequences.append({'seed':seed,'session_id':state['session_id'],'baseline_statistics':baseline['statistics'],'statistics':state['statistics'],
            'observed_detection_events':len(detections),'detected_route_proposals':len(relevant_proposals),'operations':dict(operations),
            'changed_rules_with_forwarded_packets':matches[:20],'evidence':'packet_simulation','all_events':len(events),
            'degradation_observed':max(t['statistics']['jammed_links'] for t in traces if t['seed']==seed)>0})
    payload='\n'.join(json.dumps(t,sort_keys=True) for t in traces)
    complete=all(s['observed_detection_events'] and s['detected_route_proposals'] and s['changed_rules_with_forwarded_packets'] and s['degradation_observed'] for s in sequences)
    run=write_experiment_run(base=root,experiment='swarm_live_jamming',protocol_version='swarm_live_v1',report={'sequences':sequences,'sequence_observed':bool(complete),'limitations':['Controlled packet simulation; no OpenFlow/RF/hardware accuracy claim']},seeds=[7,42,99],evidence_category='packet_simulation',config_paths=[root/'config/swarm_config.yaml',root/'config/fl_config.yaml',root/'config/rl_config.yaml',root/'config/security_config.yaml'],checkpoint_paths=[root/'models/fl_model.pth',root/'models/rl_model_v3.zip'],dataset_fingerprints={'live_packet_trace':hashlib.sha256(payload.encode()).hexdigest()},command=[sys.executable,'-m','scripts.swarm_live_validation',*sys.argv[1:]],status='passed' if complete else 'incomplete',extra_files={'trace.jsonl':payload})
    print(json.dumps({'report':str(run.report_path),'sequences':sequences,'sequence_observed':bool(complete)}))

if __name__=='__main__':main()
