"""Independent cohort/conservation and delivered-verification checks for the pilot."""
import argparse,csv,gzip,hashlib,json
from collections import Counter,defaultdict
from pathlib import Path
from simulation.swarm.experiments import source_fingerprint


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--directory',type=Path,required=True);a=p.parse_args();a.directory.mkdir(exist_ok=True,parents=True)
    state=json.loads((a.batch/'state.json').read_text());checks=[];details=[]
    for r in state['results']:
        outcomes=Counter();primary=defaultdict(lambda:dict(offered=0,delivered=0,dropped=0,latency_sum=0.));seen=set();bad=[];clock=-1.;prefix={}
        path=a.batch/r['trace'];actual=hashlib.sha256(path.read_bytes()).hexdigest();digest=hashlib.sha256()
        with gzip.open(path,'rt') as stream:
            for line in stream:
                row=json.loads(line)
                if row['kind']=='manifest':continue
                digest.update(line.encode());d=row['data'];identity=d['id'];now=d['event_at']
                if now<clock-1e-8:bad.append('event ordering')
                clock=now
                if 'base' in d['route'][1:-1]:bad.append('ground transit')
                completed=[h for h in d['history'] if h['end_at']<=now+1e-9]
                previous=prefix.get(identity,[])
                if completed[:len(previous)]!=previous:bad.append('completed hop mutated')
                prefix[identity]=completed
                if row['kind']!='packet_result':continue
                if identity in seen:bad.append('duplicate result')
                seen.add(identity);outcomes[d['status']]+=1;prefix.pop(identity,None)
                if (d.get('correlation_id') or '').startswith('primary:'):
                    m=primary[d['source']];m['offered']+=1;m[d['status']]+=1
                    if d['status']=='delivered':m['latency_sum']+=(d['completed_at']-d['created_at'])*1000
        same=all(primary[n]=={k:v for k,v in m.items() if k in primary[n]} for n,m in r['per_uav'].items())
        proof=all(h.get('verified_routes',0)>=2 and h.get('minimum_probe_spacing_s',0)>=1-1e-9 and h.get('verified_at',1e9)-h['started_at']<=3+1e-9 and h['estimates']['fleet_net_score_difference']>=-.02-1e-9 and h['estimates']['fleet_delivery_difference']>=-.02-1e-9 and h['estimates']['fleet_latency_ratio']<=1.10+1e-9 for h in r['assistance_history'] if h.get('committed_at') is not None)
        passed=not bad and actual==r['trace_sha256'] and digest.hexdigest()==r['outcome_sha256'] and same and proof and outcomes['delivered']==r['delivered'] and outcomes['dropped']==r['dropped'] and sum(outcomes.values())==r['generated']
        checks.append(dict(scenario=r['scenario'],policy=r['policy'],assistance=r['assistance'],seed=r['seed'],outcome='Passed' if passed else 'Failed',packet_results=sum(outcomes.values()),cohort_matches=same,verification_and_cost_bounds=proof,errors=sorted(set(bad)),trace_sha256=actual))
        for node,m in r['per_uav'].items():details.append(dict(scenario=r['scenario'],policy=r['policy'],seed=r['seed'],assistance=r['assistance'],node=node,**m))
    report=dict(outcome='Passed' if len(checks)==48 and all(c['outcome']=='Passed' for c in checks) else 'Failed',source_sha256=state['source_sha256'],final_source_matches=state['source_sha256']==source_fingerprint(),scope='Independently reduced all compressed packet traces: unique results, exact cohort sums, ordering, immutable completed hops, no base transit, conservation, probe spacing/deadline and recorded modeled cost bounds. Source/UI files and gzip bytes hashed separately.',checks=checks)
    (a.directory/'independent-verification.json').write_text(json.dumps(report,indent=2))
    (a.directory/'per-run-measurements.json').write_text(json.dumps(state['results'],indent=2));(a.directory/'pair-comparisons.json').write_text(json.dumps(state['pair_checks'],indent=2))
    with (a.directory/'per-uav-measurements.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(details[0]));w.writeheader();w.writerows(details)
    (a.directory/'per-run-measurements.csv').write_bytes((a.batch/'runs.csv').read_bytes())
    print(report['outcome'],sum(c['packet_results'] for c in checks),[c for c in checks if c['outcome']!='Passed'])
    if report['outcome']!='Passed' or not report['final_source_matches']:raise SystemExit(1)
if __name__=='__main__':main()
