"""Derived recovery measurements; raw execution artifacts remain immutable."""
import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean


def cohort_metrics(rows):
    delivered=[d for d in rows if d['status']=='delivered'];latencies=[(d['completed_at']-d['created_at'])*1000 for d in delivered]
    previous={};jitter=[]
    for d,latency in zip(delivered,latencies):
        key=(d['source'],d['destination'])
        if key in previous:jitter.append(abs(latency-previous[key]))
        previous[key]=latency
    offers=sorted((d['channel_key'],d['source'],d['destination'],d['category'],d['created_at'],d['size_bytes']) for d in rows)
    return dict(primary_generated=len(rows),primary_delivered=len(delivered),primary_dropped=len(rows)-len(delivered),primary_pdr=len(delivered)/len(rows) if rows else None,primary_latency_ms=mean(latencies) if latencies else None,primary_jitter_ms=mean(jitter) if jitter else None,primary_throughput_mbps=sum(d['size_bytes'] for d in delivered)*8/180/1e6,primary_hops=mean(d['hop'] for d in delivered) if delivered else None,primary_offered_sha256=hashlib.sha256(json.dumps(offers,separators=(',',':')).encode()).hexdigest())


def receipt_episodes(path):
    receipts=[];primary=[]
    with gzip.open(path,'rt') as f:
        for line in f:
            r=json.loads(line);d=r.get('data',{});key=d.get('channel_key','').split(':')
            if r['kind']=='packet_result' and d['category'] in ('telemetry','heartbeat','position','sensor') and not d.get('channel_key','').startswith('autonomy:') and d['created_at']<180:primary.append(d)
            if r['kind']=='packet_result' and d['status']=='delivered' and len(key)>=7 and key[:3]==['autonomy','response','base'] and key[4]=='False':receipts.append((d['completed_at'],d['destination']))
    receipts.sort();cursor=0;latest={};opened={};stable={};episodes=[]
    for step in range(1800):
        now=.05+step*.1
        while cursor<len(receipts) and receipts[cursor][0]<=now+1e-8:
            at,node=receipts[cursor];latest[node]=at;cursor+=1
        for n in (f'sim_uav_{i:03d}' for i in range(1,31)):
            fresh=now-latest.get(n,-100)<3-1e-9
            if fresh:
                stable.setdefault(n,now)
                if n in opened and now-stable[n]>=5-1e-9:episodes.append(dict(node=n,disconnected_at=opened.pop(n),reconnected_at=now))
            else:
                stable.pop(n,None)
                if now>=3:opened.setdefault(n,now)
    return episodes+[dict(node=n,disconnected_at=t,reconnected_at=None) for n,t in sorted(opened.items())],cohort_metrics(primary)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    state=json.loads((a.directory/'state.json').read_text());assert state['status']=='completed' and len(state['results'])==60
    a.output.mkdir(parents=True,exist_ok=True);rows=[];summaries=[]
    for r in state['results']:
        row={k:v for k,v in r.items() if k not in ('states','safety_events','recovery_episodes','controller_timings')}
        episodes,primary=receipt_episodes(a.directory/r['trace_file']);row.update(primary)
        if r['autonomy']=='on':
            success=[e for e in episodes if e['reconnected_at'] is not None]
            row.update(receipt_recovery_episodes=episodes,receipt_recovery_count=len(success),receipt_unrecovered_count=len(episodes)-len(success),receipt_recovery_mean_s=mean(e['reconnected_at']-e['disconnected_at'] for e in success) if success else None)
        else:row.update(receipt_recovery_episodes=None,receipt_recovery_count=None,receipt_unrecovered_count=None,receipt_recovery_mean_s=None)
        latched={x['data']['node'] for x in r['safety_events'] if x['kind']=='safety_latched'}
        row['latched_without_completed_landing_at_180s']=sorted(latched-set(r['landing_outcomes']))
        row['packet_delivery_ratio']=r['delivered']/r['generated'] if r['generated'] else None
        rows.append(row)
    for scenario in sorted({r['scenario'] for r in rows}):
        off=sorted([r for r in rows if r['scenario']==scenario and r['autonomy']=='off'],key=lambda r:r['seed']);on=sorted([r for r in rows if r['scenario']==scenario and r['autonomy']=='on'],key=lambda r:r['seed'])
        for x,y in zip(off,on):assert x['seed']==y['seed'] and x['shared_input_sha256']==y['shared_input_sha256'] and x['primary_offered_sha256']==y['primary_offered_sha256']
        summaries.append(dict(scenario=scenario,paired_seeds=[r['seed'] for r in on],valid_pairs=3,off_primary_pdr=mean(r['primary_pdr'] for r in off),on_primary_pdr=mean(r['primary_pdr'] for r in on),primary_delivery_difference_pp=mean((y['primary_pdr']-x['primary_pdr'])*100 for x,y in zip(off,on)),off_delivery_availability=mean(r['delivered_network_availability'] for r in off),on_delivery_availability=mean(r['delivered_network_availability'] for r in on),paired_availability_difference_pp=mean((y['delivered_network_availability']-x['delivered_network_availability'])*100 for x,y in zip(off,on)),on_verified_roundtrip_availability=mean(r['verified_roundtrip_availability'] for r in on),off_cpu_s=mean(r['cpu_s'] for r in off),on_cpu_s=mean(r['cpu_s'] for r in on),prevented_transitions=sum(r['prevented_transitions'] for r in on),transition_failures=sum(r['failed_transitions'] for r in on),on_switches=sum(r['switches'] for r in on),off_switches=sum(r['switches'] for r in off),reverse_switches_30s=sum(r['reverse_switches_30s'] for r in on),completed_landings=sum(r['safety_counters'].get('landings',0) for r in on),ongoing_latched_nodes=sum(len(r['latched_without_completed_landing_at_180s']) for r in on),unrecovered_receipt_episodes=sum(r['receipt_unrecovered_count'] for r in on),extra_holds_against_scripted_failsafe_expectation=sum(len(r['failsafe_false_positive_nodes']) for r in on),missed_safety_onsets=sum(len(r['failsafe_missed_nodes']) for r in on)))
    result=dict(protocol=state['protocol'],execution_source_sha256=state['source_sha256'],runs=60,packet_results=sum(r['generated'] for r in rows),all_conserved=all(r['conservation'] for r in rows),note='Exploratory three-seed pairs per scenario. Primary cohort is telemetry/heartbeat/position/sensor offered before 180s, reduced through the TTL drain; offered hashes match each pair. Latency/hops condition on delivery; jitter uses successive delivered latency differences per source/destination, as in existing experiment summaries. All-packet delivery ratios include added recovery control traffic and must not be interpreted as this common cohort. Receipt episodes are independently reconstructed at 0.1s resolution because a safety latch can mask state-only isolation transitions. No raw results were overwritten.',summaries=summaries)
    (a.output/'study-summary.json').write_text(json.dumps(result,indent=2));(a.output/'per-run-measurements.json').write_text(json.dumps(rows,indent=2))
    fields=list(rows[0])
    with (a.output/'per-run-measurements.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows)
    print(json.dumps({k:v for k,v in result.items() if k!='summaries'},indent=2))

if __name__=='__main__':main()
