"""Separate 60-run, 30-UAV recovery validation; never mutates the live mission."""
import argparse
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor,as_completed
from copy import deepcopy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import time
import yaml

from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.recovery_scenarios import PRESETS,schedule


def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def run_one(config,scenario,enabled,seed,directory):
    cfg=deepcopy(config);cfg.update(default_count=30,seed=seed,autonomy_mode='recovery_v1' if enabled else 'off',topology_mode='auto',logical_channel_keys=True)
    e=SwarmEngine([],cfg,isolated=True);e.scenario=scenario
    shared=digest(dict(fleet=[dict(id=d.id,position=d.position) for d in e.nodes.values()],seed=seed,scenario=scenario,schedule=schedule(scenario),radio=cfg['radio'],packets=cfg['packets']))
    mode='on' if enabled else 'off';path=Path(directory)/f'{scenario}-{mode}-{seed}.jsonl.gz'
    packets=Counter();delivery={};events=Counter();states=[];switches=[];safety=[];queue_peak=0;availability=[];verified=[];partition=[];disconnected=defaultdict(float);event_cursor=0
    wall=time.perf_counter();cpu=time.process_time();trace_hash=hashlib.sha256();measured_safety=None;measured_local=None;measured_outcomes=None;measured_unresolved=None
    with gzip.open(path,'wt',compresslevel=5) as trace:
        trace.write(json.dumps(dict(kind='manifest',protocol='autonomous_recovery_validation_v1',scenario=scenario,enabled=enabled,seed=seed,shared_input_sha256=shared))+'\n')
        def write(row):
            wire=json.dumps(row,sort_keys=True,separators=(',',':'));trace.write(wire+'\n');trace_hash.update(wire.encode()+b'\n')
        def sink(record):
            data=record['data'];kind=record['kind']
            if kind=='packet_result':
                packets[data['status']]+=1
                if data['status']=='delivered' and data['destination']=='base':delivery[data['source']]=data['completed_at']
            # Full packet traces, excluding random session identity and wall time.
            write(dict(kind=kind,simulation_time=record['simulation_time'],data=data))
        e.trace_sink=sink;e.playing=True
        for step in range(round((180+cfg['packets']['ttl_s'])/.05)):
            if e.seconds>=180-1e-9 and not getattr(e,'validation_draining',False):
                e.validation_draining=True;e.generate_traffic=lambda:None
                measured_safety=Counter()
                for supervisor in e.flight_safety.supervisors.values():measured_safety.update(supervisor.counters)
                measured_local=deepcopy(e.autonomy.controllers)
                measured_outcomes={n:s.outcome for n,s in e.flight_safety.supervisors.items() if s.outcome}
                measured_unresolved={n:s.unresolved[:] for n,s in e.flight_safety.supervisors.items() if s.unresolved}
                # Stop new recovery offers during the TTL drain; existing
                # packets, safety and scheduler outcomes continue normally.
                e.autonomy.last_send=float('inf')
            e.tick(.05)
            for event in e.events:
                if event['cursor']<=event_cursor:continue
                kind=event['kind'];data=event['data'];events[kind]+=1
                if event['category'] in ('SAFETY','RECOVERY') or kind.startswith('topology_'):
                    row=dict(kind=kind,simulation_time=event['simulation_time'],data=data);write(row)
                    if event['category']=='SAFETY' and event['simulation_time']<180:safety.append(row)
                    if kind=='local_state_changed' and event['simulation_time']<180:states.append(row)
                    if kind=='topology_transition_completed' and event['simulation_time']<180:switches.append(row)
            event_cursor=e.event_cursor
            queue_peak=max(queue_peak,len(e.scheduler.packets))
            if step%4==0 and e.seconds<180:
                availability.append(sum(e.seconds-delivery.get(n,-100)<3 for n in e.nodes)/len(e.nodes))
                if enabled:verified.append(sum(bool(c.recent_ground(e.seconds)) for c in e.autonomy.controllers.values())/len(e.nodes))
                for n in e.nodes:
                    if e.seconds-delivery.get(n,-100)>=3:disconnected[n]+=.2
                partition.append(e.connected_components())
            if step%100==0:
                if (Path(directory)/'STOP').exists():raise RuntimeError('Validation stopped: source repair required; partial evidence retained')
                trace.flush()
                if shutil.disk_usage(directory).free<512*1024**2:raise RuntimeError('Storage reserve reached; completed runs retained')
        if e.scheduler.packets:raise RuntimeError('TTL drain left pending packets')
    actual_targets=list(e.nodes)[:5] if scenario in ('recovery_total_loss','recovery_multiple_isolation') else list(e.nodes)[:1]
    expected=scenario in ('recovery_total_loss','recovery_intermittent','recovery_multiple_isolation','recovery_low_battery_isolation','recovery_failed_recovery_landing','recovery_switch_instability')
    latches={r['data']['node']:r['simulation_time'] for r in safety if r['kind']=='safety_latched'}
    expected_onset=8. if scenario=='recovery_low_battery_isolation' else 11.
    expected_checks={n:bool(n in latches and abs(latches[n]-expected_onset)<=1.5) for n in actual_targets} if expected and enabled else None
    safety_counts=Counter()
    safety_counts.update(measured_safety or {})
    episodes=[];open_episodes={}
    for event in states:
        node=event['data']['node'];value=event['data']['state'];at=event['simulation_time']
        if value in ('PARTIALLY_DISCONNECTED','COMPLETELY_ISOLATED') and node not in open_episodes:
            open_episodes[node]=at
        if value=='RECONNECTED' and node in open_episodes:
            episodes.append(dict(node=node,disconnected_at=open_episodes.pop(node),reconnected_at=at))
    episodes += [dict(node=n,disconnected_at=at,reconnected_at=None) for n,at in sorted(open_episodes.items())]
    recovered=[v for v in episodes if v['reconnected_at'] is not None]
    false_positive=sorted(set(latches)-(set(actual_targets) if expected else set()))
    misses=sorted(n for n,ok in (expected_checks or {}).items() if not ok)
    reverse=sum(a['data'].get('topology')==c['data'].get('topology') and c['simulation_time']-a['simulation_time']<=30 for a,b,c in zip(switches,switches[1:],switches[2:]))
    result=dict(protocol='autonomous_recovery_validation_v1',scenario=scenario,autonomy=mode,seed=seed,shared_input_sha256=shared,
        source_sha256=source_fingerprint(),duration_s=e.seconds,generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets),
        conservation=e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped,
        delivered_network_availability=sum(availability)/len(availability),verified_roundtrip_availability=sum(verified)/len(verified) if verified else None,
        disconnected_seconds=dict(disconnected),partition_samples=sum(c>1 for c in partition),partition_onsets=sum(c>1 and (i==0 or partition[i-1]<=1) for i,c in enumerate(partition)),
        local_recovery_success=sum(c.stable and c.prior_loss for c in (measured_local or {}).values()) if enabled else None,
        recovery_episodes=episodes,recovery_success_rate=len(recovered)/len(episodes) if episodes else None,
        mean_recovery_s=sum(v['reconnected_at']-v['disconnected_at'] for v in recovered)/len(recovered) if recovered else None,unrecovered_episodes=len(episodes)-len(recovered),
        failsafe_false_positive_nodes=false_positive,failsafe_missed_nodes=misses,
        failsafe_activation_accuracy=(30-len(false_positive)-len(misses))/30 if enabled else None,
        recovery_attempts=sum(c.attempts for c in (measured_local or {}).values()) if enabled else None,
        failed_transitions=events['topology_transition_failed'],prevented_transitions=e.autonomy.counters['prevented_transitions'],switches=len(switches),reverse_switches_30s=reverse,
        safety_counters=dict(safety_counts),failsafe_onset_checks=expected_checks,safety_events=safety,states=states,
        unresolved=measured_unresolved,landing_outcomes=measured_outcomes,drain_note='Flight/liveness measurements close at 180s; packet TTL accounting includes the final drain.',
        control_traffic=dict(e.autonomy.counters),queue_peak=queue_peak,cpu_s=time.process_time()-cpu,wall_s=time.perf_counter()-wall,
        trace_file=path.name,trace_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),outcome_sha256=trace_hash.hexdigest(),
        controller_timings=e.performance.snapshot(e.playing,e.speed)['timings'],
        evidence='modeled_packet_and_kinematic_safety_simulation',trajectory_pairing='initial_inputs_only_failsafe_motion_is_endogenous')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--workers',type=int,default=2,choices=(1,2));args=p.parse_args()
    args.directory.mkdir(parents=True,exist_ok=False);config=yaml.safe_load(Path('config/swarm_config.yaml').read_text());source=source_fingerprint()
    if shutil.disk_usage(args.directory).free<1024**3:raise RuntimeError('At least 1 GiB free required before batch start')
    files=[*Path('simulation/swarm').glob('*.py'),*Path('schemas').glob('*.py'),Path('config/swarm_config.yaml'),Path('frontend-react/src/mission-simulation/assets/terrain/heightmap.ts'),Path('scripts/run_autonomous_recovery.py')]
    with tarfile.open(args.directory/'execution-source.tar.gz','w:gz') as tar:
        for path in files:tar.add(path,arcname=path)
    state=dict(protocol='autonomous_recovery_validation_v1',status='running',source_sha256=source,configuration=config,workers=args.workers,results=[],failures=[],total_runs=60)
    jobs=[(name,enabled,seed) for name in PRESETS for seed in (7,42,99) for enabled in (False,True)]
    def save():
        tmp=args.directory/'state.tmp';tmp.write_text(json.dumps(state,indent=2));tmp.replace(args.directory/'state.json')
    save()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(run_one,config,name,enabled,seed,args.directory):(name,enabled,seed) for name,enabled,seed in jobs}
        for future in as_completed(pending):
            try:
                row=future.result();state['results'].append(row);print(len(state['results']),row['scenario'],row['autonomy'],row['seed'],round(row['cpu_s'],2),flush=True)
            except Exception as exc:state['failures'].append(dict(case=pending[future],error=str(exc)));print('FAILED',pending[future],str(exc),flush=True)
            save()
    state['status']='completed' if len(state['results'])==60 and not state['failures'] else 'failed'
    if source_fingerprint()!=source:state['status']='failed';state['failures'].append(dict(error='Source changed during execution'))
    state['results'].sort(key=lambda r:(r['scenario'],r['seed'],r['autonomy']));save()
    fields=[k for k,v in state['results'][0].items() if not isinstance(v,(dict,list))] if state['results'] else []
    with (args.directory/'measurements.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(state['results'])
    if state['status']!='completed':raise RuntimeError('Validation batch incomplete; see retained state and failures')

if __name__=='__main__':main()
