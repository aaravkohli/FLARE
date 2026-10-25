"""Process-isolated, reproducible mesh-only comparisons and benchmark jobs."""
from __future__ import annotations
from copy import deepcopy
import csv
import gzip
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
from queue import Empty
import time
import uuid
from schemas.mission import TOPOLOGIES, ExperimentRequest
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.scenarios import DEMO_PRESETS, schedule

SIMULATOR_VERSION='flare_mesh_v2'
CSV_FIELDS=['topology','seed','generated','delivered','dropped','pdr','average_latency_ms','jitter_ms','throughput_mbps','average_hops','connected_components','failed_routes','route_convergence_ms','service_recovery_ms','unrecovered_flows','conservation']

def reference_engine(records,config,request,topology,seed):
    cfg=deepcopy(config)
    cfg.update(seed=seed,topology=topology,topology_mode='manual',default_count=request.count,formation='distributed',mesh_only=True,traffic_stop_s=request.duration_s)
    cfg['radio']['range_m']=request.range_m
    e=SwarmEngine(records,cfg,isolated=True)
    e.scenario=request.scenario
    ids=list(e.nodes)
    def offered():
        if e.seconds>=request.duration_s: return
        for i,source in enumerate(ids):
            e.send(source,ids[(i+len(ids)//2)%len(ids)],'sensor',512,'reference',family_override='mesh')
    e.generate_traffic=offered
    return e

def summarize(e,request,topology,seed,cohort):
    rows=[p for p in cohort if request.warmup_s<=p['created_at']<request.duration_s]
    delivered=[p for p in rows if p['status']=='delivered']
    latencies=[(p['completed_at']-p['created_at'])*1000 for p in delivered]
    by_flow={}; jitter=[]
    for p,latency in zip(delivered,latencies):
        key=(p['source'],p['destination'])
        if key in by_flow: jitter.append(abs(latency-by_flow[key]))
        by_flow[key]=latency
    route_times=[r['route_convergence_ms'] for r in e.recovery.values() if r.get('route_convergence_ms') is not None]
    service_times=[r['service_recovery_ms'] for r in e.recovery.values() if r.get('service_recovery_ms') is not None]
    config_digest=hashlib.sha256(json.dumps(e.cfg,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(configuration_sha256=config_digest,route_convergence_ms=sum(route_times)/len(route_times) if route_times else None,
        service_recovery_ms=sum(service_times)/len(service_times) if service_times else None,
        unrecovered_flows=sum(r.get('service_recovered_at') is None for r in e.recovery.values()),topology=topology,seed=seed,generated=len(rows),delivered=len(delivered),dropped=len(rows)-len(delivered),
        pdr=len(delivered)/len(rows) if rows else None,
        average_latency_ms=sum(latencies)/len(latencies) if latencies else None,
        jitter_ms=sum(jitter)/len(jitter) if jitter else None,
        throughput_mbps=sum(p['size_bytes'] for p in delivered)*8/(request.duration_s-request.warmup_s)/1e6,
        average_hops=sum(p['hop'] for p in delivered)/len(delivered) if delivered else None,
        connected_components=e.connected_components(),failed_routes=e.failed_routes,recovery=list(e.recovery.values()),
        conservation=e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets))

def _atomic_json(filename,value):
    temporary=filename.with_suffix('.tmp'); temporary.write_text(json.dumps(value,allow_nan=False)); temporary.replace(filename)

def source_fingerprint():
    root=Path(__file__).resolve().parents[2]
    names=['schemas/mission.py', 'simulation/swarm/engine.py', 'simulation/swarm/network.py',
           'simulation/swarm/topology.py', 'simulation/swarm/adaptive_topology.py', 'simulation/swarm/movement.py', 'simulation/swarm/attacks.py',
           'simulation/swarm/scenarios.py', 'simulation/swarm/terrain.py', 'simulation/swarm/experiments.py']
    digest=hashlib.sha256()
    for name in names: digest.update(name.encode()+b'\0'+(root/name).read_bytes()+b'\0')
    return digest.hexdigest()

def _worker(identity,payload,records,config,directory,commands):
    request=ExperimentRequest.model_validate(payload); directory=Path(directory)
    base=dict(id=identity,mode=request.mode,configuration=payload,simulation_config=deepcopy(config),fleet_records=deepcopy(records),
        source_sha256=source_fingerprint(),simulator_version=SIMULATOR_VERSION,
        routing_policy='mesh_only_trust_aware_shortest_path',evidence='packet_simulation',
        scenario_schedule=schedule(request.scenario),model_inference='not_run_reference_policy',results=[],status='paused',progress=0.)
    filename=directory/'state.json'
    engines=[]; traces=[]; cohorts=[]
    def setup(topologies,seed):
        for trace in traces: trace.close()
        traces.clear(); cohorts.clear(); engines.clear()
        for topology in topologies:
            e=reference_engine(records,config,request,topology,seed); cohort=[]
            trace=gzip.open(directory/f'{topology}-{seed}.jsonl.gz','wt'); traces.append(trace); cohorts.append(cohort)
            def sink(event,trace=trace,cohort=cohort):
                trace.write(json.dumps(event,separators=(',',':'))+'\n')
                data=event['data']
                if event['kind']=='packet_result' and data.get('correlation_id')=='reference': cohort.append(data)
            e.trace_sink=sink; engines.append(e)
        base['terrain_provenance']=deepcopy(engines[0].terrain.provenance)
    def publish(include_runs=False):
        state=dict(base)
        if include_runs: state['runs']=[e.snapshot(include_flows=True) for e in engines]
        _atomic_json(filename,state)
    try:
        if request.mode=='compare':
            setup(request.topologies,request.seeds[0]); publish(True)
            previous=time.monotonic(); last_publish=0.
            while base['status'] not in ('cancelled','completed'):
                try:
                    while True:
                        action=commands.get_nowait()
                        if action=='reset':
                            setup(request.topologies,request.seeds[0]); base.update(status='paused',progress=0.,results=[])
                        else: base['status']={'play':'running','pause':'paused','cancel':'cancelled'}[action]
                        for e in engines: e.playing=base['status']=='running'
                except Empty: pass
                now=time.monotonic(); delta=min(.1,now-previous); previous=now
                if base['status']=='running':
                    for e in engines: e.tick(delta)
                    base['progress']=min(1.,engines[0].seconds/(request.duration_s+config['packets']['ttl_s']))
                    if engines[0].seconds>=request.duration_s+config['packets']['ttl_s']:
                        base['status']='completed'
                        base['results']=[summarize(e,request,t,request.seeds[0],cohort) for e,t,cohort in zip(engines,request.topologies,cohorts)]
                if now-last_publish>=.2:
                    publish(True); last_publish=now
                time.sleep(.01)
            publish(True)
        else:
            base['status']='running'; publish()
            jobs=[(t,seed) for seed in request.seeds for t in request.topologies]
            for index,(topology,seed) in enumerate(jobs):
                setup([topology],seed); e=engines[0]; e.playing=True
                total_steps=round((request.duration_s+config['packets']['ttl_s'])/config['fixed_step_s'])
                for step in range(total_steps):
                    if step%20==0:
                        try:
                            action=commands.get_nowait()
                            if action=='cancel': base['status']='cancelled'; publish(); return
                        except Empty: pass
                    e.tick(config['fixed_step_s'])
                    if step%100==0:
                        base.update(progress=(index+step/total_steps)/len(jobs),current_topology=topology,current_seed=seed)
                        publish()
                base['results'].append(summarize(e,request,topology,seed,cohorts[0]))
            base.update(status='completed',progress=1.); publish()
        for trace in traces: trace.flush()
        base['trace_files']=[p.name for p in sorted(directory.glob('*.jsonl.gz'))]
        # Summaries are reproducible; UUIDs and wall timestamps remain trace metadata.
        canonical=json.dumps(base['results'],sort_keys=True,separators=(',',':'))
        base['results_sha256']=hashlib.sha256(canonical.encode()).hexdigest()
        publish(request.mode=='compare')
    except Exception as exc:
        base.update(status='failed',error=str(exc)); publish()
    finally:
        for trace in traces: trace.close()

class ExperimentManager:
    def __init__(self,directory):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True); self.jobs={}
    def create(self,request,engine):
        if request.scenario not in DEMO_PRESETS: raise ValueError('unknown demonstration scenario')
        if sum(job['process'].is_alive() for job in self.jobs.values())>=2:
            raise ValueError('two experiments are already active; cancel or finish one first')
        if request.mode=='benchmark': request=request.model_copy(update={'topologies':list(TOPOLOGIES)})
        for old,job in list(self.jobs.items()):
            if not job['process'].is_alive():
                job['process'].join(timeout=0); job['commands'].close(); self.jobs.pop(old)
        identity=uuid.uuid4().hex; directory=self.directory/identity; directory.mkdir()
        ctx=multiprocessing.get_context('spawn'); commands=ctx.Queue()
        process=ctx.Process(target=_worker,args=(identity,request.model_dump(),list(engine.records.values()),engine.cfg,str(directory),commands),daemon=True)
        initial=dict(id=identity,status='starting',mode=request.mode,configuration=request.model_dump(),results=[],progress=0.)
        _atomic_json(directory/'state.json',initial)
        process.start(); self.jobs[identity]=dict(process=process,commands=commands)
        return initial
    def state(self,identity):
        if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity): raise ValueError('unknown experiment')
        filename=self.directory/identity/'state.json'
        if not filename.is_file(): raise ValueError('unknown experiment')
        state=json.loads(filename.read_text())
        job=self.jobs.get(identity)
        if state['status'] in ('starting','running','paused') and (not job or not job['process'].is_alive()):
            state.update(status='failed',error='Experiment worker stopped; retained results remain available')
        return state
    def control(self,identity,action):
        state=self.state(identity)
        if state['status'] in ('completed','cancelled','failed'): raise ValueError('experiment has finished')
        if state['mode']=='benchmark' and action!='cancel': raise ValueError('benchmarks support cancellation only')
        self.jobs[identity]['commands'].put(action)
        return dict(id=identity,accepted=True,action=action)
    def export(self,identity,format):
        state=self.state(identity)
        if format=='json': return json.dumps(state,indent=2)
        if format!='csv': raise ValueError('unsupported export format')
        fields=CSV_FIELDS
        output=io.StringIO(); writer=csv.DictWriter(output,fieldnames=fields,extrasaction='ignore'); writer.writeheader(); writer.writerows(state.get('results',[])); return output.getvalue()
    def close(self):
        for job in self.jobs.values():
            if job['process'].is_alive(): job['process'].terminate()
            job['process'].join(timeout=2); job['commands'].close()
