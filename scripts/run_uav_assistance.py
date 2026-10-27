"""Isolated 48-run exploratory assistance batch; no model inference or live state."""
import argparse
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor,as_completed
from copy import deepcopy
from dataclasses import replace
import csv,gzip,hashlib,json,platform,shutil,tarfile,time
from pathlib import Path
import yaml
from schemas.mission import MissionConfig,MissionAttack
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.topology import pair

SCENARIOS=('recoverable_mesh','direct_only','total_isolation','competing_affected')
PROTOCOL='affected_uav_assist_exploratory_v1'
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def fixture(config,scenario,enabled,policy,seed):
    cfg=deepcopy(config);cfg.update(default_count=30,seed=seed,topology='tree',topology_mode='manual',adaptive_policy=policy,affected_uav_assist=enabled,autonomy_mode='off',logical_channel_keys=True,observation_stop_s=60)
    cfg['radio'].update(range_m=1000,reference_rssi_dbm=-20,noise_dbm=-110,propagation_ms=8)
    e=SwarmEngine([],cfg,isolated=True);ids=list(e.nodes)
    for node in e.nodes.values():node.mode='hold'
    pairs=[(ids[15],ids[29])]
    if scenario=='competing_affected':pairs.append((ids[14],ids[28]))
    weak=set()
    for a,b in pairs:
        path=e.route(a,b,'mesh');weak.update(pair(x,y) for x,y in zip(path,path[1:]))
    original=e.attacks.impair
    def impairment(link,family,endpoints):
        l=original(link,family,endpoints)
        if l and family=='mesh' and scenario in ('recoverable_mesh','competing_affected') and 8<=e.seconds<60 and pair(*endpoints) in weak:
            return replace(l,latency_ms=l.latency_ms+22,status='degraded')
        return l
    e.attacks.impair=impairment
    offered=hashlib.sha256();sequence=0
    def traffic():
        nonlocal sequence
        if e.seconds>=60-1e-9:return
        sequence+=1
        for i,node in enumerate(ids):
            special=next((b for a,b in pairs if a==node),None)
            peer=special or (ids[max(0,(i-1)//2)] if i else ids[1])
            if peer in [a for a,b in pairs]:peer=ids[0]
            for j in range(8 if special else 1):
                key=f'primary:{node}:{sequence}:{j}';offered.update(f'{key}:{peer}:512\n'.encode())
                e.send(node,peer,'sensor',512,key,family_override='mesh',channel_key=key,_mission_offer=True)
        # Separate Direct traffic demonstrates family-specific impairment.
        if scenario=='direct_only':
            key=f'direct:{sequence}'
            e.send(ids[15],'base','telemetry',512,key,family_override='direct',channel_key=key,_mission_offer=True)
    e.generate_traffic=traffic
    spec=dict(protocol=PROTOCOL,scenario=scenario,seed=seed,initial_fleet=[dict(id=n.id,position=n.position) for n in e.nodes.values()],radio=cfg['radio'],packets=cfg['packets'],pairs=pairs,weak_edges=sorted(weak),manual_warmup_s=6,auto_start_s=6,traffic_stop_s=60,fault_onset_s=8,
        fault='Mesh link propagation +22ms on declared incumbent path edges' if scenario in ('recoverable_mesh','competing_affected') else 'Direct spot-jam condition for 52s' if scenario=='direct_only' else 'All three interfaces unavailable for target',trajectory='stationary modeled flight HOLD, not network HOLD')
    return e,spec,offered,pairs


def run_one(config,scenario,enabled,policy,seed,directory):
    e,spec,offered,pairs=fixture(config,scenario,enabled,policy,seed);key=f'{scenario}-{policy}-{int(enabled)}-{seed}'
    path=Path(directory)/(key+'.jsonl.gz');measure=defaultdict(lambda:dict(offered=0,delivered=0,dropped=0,latency_sum=0.));events=[];cursor=0;peak=0;tracehash=hashlib.sha256();commit=None;cpu=time.process_time();wall=time.perf_counter()
    with gzip.open(path,'wt',compresslevel=5) as trace:
        trace.write(json.dumps(dict(kind='manifest',shared_inputs=spec,policy=policy,assistance=enabled))+'\n')
        def sink(row):
            nonlocal commit
            data=row['data'];wire=json.dumps(dict(kind=row['kind'],simulation_time=row['simulation_time'],data=data),sort_keys=True,separators=(',',':'));tracehash.update(wire.encode()+b'\n');trace.write(wire+'\n')
            correlation=data.get('correlation_id') or ''
            if not correlation.startswith('primary:'):return
            if row['kind']=='packet_queued':measure[data['source']]['offered']+=1
            if row['kind']=='packet_result':
                m=measure[data['source']];m[data['status']]+=1
                if data['status']=='delivered':m['latency_sum']+=(data['completed_at']-data['created_at'])*1000
        e.trace_sink=sink;e.playing=True
        for step in range(round((60+e.cfg['packets']['ttl_s'])/.05)):
            if step==120:e.configure(MissionConfig(expected_revision=e.revision,topology_mode='auto'))
            if step==160:
                if scenario=='direct_only':e.inject_attack(MissionAttack(session_id=e.session_id,expected_revision=e.revision,attack_id='direct-only-fixture',profile='spot',targets=[pairs[0][0]],paths=['direct'],duration_s=52))
                if scenario=='total_isolation':e.autonomy.interfaces[pairs[0][0]]=dict(direct=False,satellite=False,mesh=False)
                e.event('NETWORK','assistance_fixture_onset',dict(scenario=scenario,targets=[a for a,b in pairs],specification=spec['fault']))
            if step==1200:
                e.adaptive.cancel('Exploratory measurement ended; TTL drain only');e.topology_mode='manual';e.assistance.winner=None;e.assistance.wins=0
            e.tick(.05);peak=max(peak,len(e.scheduler.packets))
            for event in e.events:
                if event['cursor']>cursor and (event['kind'].startswith('uav_assistance') or event['kind'].startswith('topology_')):
                    events.append({k:v for k,v in event.items() if k not in ('timestamp','session_id')})
            cursor=e.event_cursor
            if step%100==0:
                trace.flush()
                if shutil.disk_usage(directory).free<512*1024**2:raise RuntimeError('Storage reserve reached; partial traces retained')
        assert not e.scheduler.packets
    def quality(m):
        return dict(**m,pdr=m['delivered']/m['offered'] if m['offered'] else None,latency_ms=m['latency_sum']/m['delivered'] if m['delivered'] else None)
    overall=dict(offered=0,delivered=0,dropped=0,latency_sum=0.)
    for m in measure.values():
        for k,v in m.items():overall[k]+=v
    return dict(protocol=PROTOCOL,scenario=scenario,assistance=enabled,policy=policy,seed=seed,shared_input_sha256=digest(spec),offered_sha256=offered.hexdigest(),
        initial_topology='tree',final_topology=e.topology,primary=quality(overall),targets={a:quality(measure[a]) for a,b in pairs},per_uav={n:quality(m) for n,m in measure.items()},
        assistance_counters=dict(e.assistance.counters),assistance_history=list(e.assistance.history),transition_failures=sum(ev['kind']=='topology_transition_failed' for ev in events),switches=sum(ev['kind']=='topology_transition_completed' for ev in events),
        generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets),conservation=e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped,
        control_traffic=dict(e.autonomy.counters),protected_airtime=dict(e.scheduler.airtime_totals),queue_peak=peak,events=events,trace=path.name,outcome_sha256=tracehash.hexdigest(),trace_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        cpu_s=time.process_time()-cpu,wall_s=time.perf_counter()-wall,timings=e.performance.snapshot(True,1)['timings'],source_sha256=source_fingerprint(),evidence='Modeled packet simulation; exploratory fixture, no detector inference',
        latency_conditioning='Delivered primary packets only; all offers include failures in PDR',unresolved_quality='A verified route does not establish quality improvement; see assistance_history')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--workers',type=int,default=2,choices=(1,2));a=p.parse_args();a.directory.mkdir(parents=True,exist_ok=False)
    if shutil.disk_usage(a.directory).free<1024**3:raise RuntimeError('1 GiB free reserve required')
    config=yaml.safe_load(Path('config/swarm_config.yaml').read_text());source=source_fingerprint()
    with tarfile.open(a.directory/'execution-source.tar.gz','w:gz') as tar:
        for file in [*Path('simulation/swarm').glob('*.py'),*Path('schemas').glob('*.py'),Path('config/swarm_config.yaml'),Path(__file__)]:tar.add(file,arcname=file)
    state=dict(protocol=PROTOCOL,source_sha256=source,configuration=config,status='running',outcome='Not Tested',results=[],failures=[],total_runs=48,hardware=platform.uname()._asdict(),note='Six-second fixed-tree bootstrap, Auto enabled at T+6s; fault onset T+8s, primary offers T<60s, then TTL drain. Protected report workload follows each selected policy. Only off/on pairs within the same policy are paired.')
    def save():
        (a.directory/'state.tmp').write_text(json.dumps(state,indent=2));(a.directory/'state.tmp').replace(a.directory/'state.json')
    save()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        jobs={pool.submit(run_one,config,s,on,pol,seed,a.directory):(s,on,pol,seed) for s in SCENARIOS for pol in ('balanced_v1','evidence_resilience_v1') for seed in (7,42,99) for on in (False,True)}
        for f in as_completed(jobs):
            try:
                row=f.result();state['results'].append(row);print(jobs[f],row['switches'],row['assistance_counters'],flush=True)
            except Exception as exc:state['failures'].append(dict(case=jobs[f],error=repr(exc)))
            save()
    checks=[]
    for row in state['results']:
        if not row['assistance']:continue
        old=next((r for r in state['results'] if (r['scenario'],r['policy'],r['seed'],r['assistance'])==(row['scenario'],row['policy'],row['seed'],False)),None)
        checks.append(dict(scenario=row['scenario'],policy=row['policy'],seed=row['seed'],inputs_match=bool(old and old['shared_input_sha256']==row['shared_input_sha256'] and old['offered_sha256']==row['offered_sha256']),conservation=row['conservation'] and bool(old and old['conservation']),fleet_delivery_difference_pp=(row['primary']['pdr']-old['primary']['pdr'])*100 if old else None,fleet_latency_difference_ms=row['primary']['latency_ms']-old['primary']['latency_ms'] if old and row['primary']['latency_ms'] is not None and old['primary']['latency_ms'] is not None else None))
    state.update(status='completed' if not state['failures'] else 'failed',outcome='Passed' if not state['failures'] and len(state['results'])==48 and all(c['inputs_match'] and c['conservation'] for c in checks) else 'Failed',pair_checks=checks)
    if source_fingerprint()!=source:state.update(outcome='Failed',source_changed=True)
    save()
    with (a.directory/'runs.csv').open('w') as stream:
        fields=['scenario','policy','seed','assistance','pdr','latency_ms','switches','transition_failures','cpu_s','generated','delivered','dropped','conservation','shared_input_sha256','offered_sha256'];writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for r in state['results']:writer.writerow({k:r.get(k,r['primary'].get(k)) for k in fields})

    if state['outcome']!='Passed':raise SystemExit(1)

if __name__=='__main__':main()
