"""Immutable adaptive_resilience_v1 study and isolated, streamed batch execution."""
from __future__ import annotations
from copy import deepcopy
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
from queue import Empty
import shutil
import time

from schemas.mission import AdaptiveStudyRequest, ExperimentRequest, TOPOLOGIES
from simulation.swarm.reference_workload import REFERENCE_WORKLOAD, LEGACY_LIMITATION
from simulation.swarm.scenarios import schedule
from simulation.swarm.study_statistics import statistical_report

PROTOCOL_ID='adaptive_resilience_v1'
SCENARIOS=('normal','mobility','regional_jamming','congestion','partition_reconnection')
ARMS=('auto',*TOPOLOGIES)
PRESETS={'pilot':(7,42,99),'evidence':tuple(range(1001,1011))}
STUDY_CSV=['protocol_id','reference_workload','scenario','policy_id','seed','initial_topology','final_topology',
    'generated','delivered','dropped','pdr','average_latency_ms','jitter_ms','throughput_mbps','average_hops',
    'connected_components','failed_routes','route_convergence_ms','service_recovery_ms','unrecovered_flows',
    'switches','transition_failures','deferred_packets','deferred_dropped','resource_cost','background_offered',
    'background_transmissions','queue_peak','queue_overflows','conservation','fleet_sha256','shared_input_sha256','outcome_sha256']


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def protocol(preset,ttl_s):
    if preset not in PRESETS: raise ValueError('unknown study preset')
    if ttl_s!=20: raise ValueError('adaptive_resilience_v1 requires the versioned 20-second packet TTL')
    return dict(id=PROTOCOL_ID,preset=preset,exploratory=preset=='pilot',count=30,seeds=list(PRESETS[preset]),
        scenarios=list(SCENARIOS),policies=list(ARMS),range_m=350.,warmup_s=5.,measured_traffic_s=60.,
        traffic_stop_s=65.,ttl_drain_s=ttl_s,total_simulation_s=65.+ttl_s,
        total_runs=len(PRESETS[preset])*len(SCENARIOS)*len(ARMS),reference_workload=REFERENCE_WORKLOAD,
        partition_mechanism='policy-imposed link cut, not physical movement',
        primary_comparison='Auto minus fixed Self-healing Mesh delivery ratio',bootstrap_resamples=10000,
        reference_policy='mesh_only_trust_aware_shortest_path',model_inference='excluded',
        nominal_demand='512 bytes per UAV per second to opposite-index peer; estimates before offers',
        congestion='five peer flows, 100 packets/s each, 4096 bytes, [8,25) simulation seconds')


def storage_preflight(directory, remaining, sample_sizes=()):
    """Pilot-size projection plus a reserve; never remove unrelated files."""
    free=shutil.disk_usage(directory).free
    estimate=max(sample_sizes,default=8*1024**2) # conservative until a streamed run supplies evidence
    required=remaining*estimate*1.3+512*1024**2
    if free<required:
        raise ValueError(f'Insufficient study storage: {free/1024**3:.2f} GiB free; {required/1024**3:.2f} GiB projected including reserve. Retained results are available.')
    return dict(free_bytes=free,projected_required_bytes=int(required),sample_trace_bytes=estimate)


def run_one(records,config,scenario,policy,seed,directory,cancel=lambda:False,progress=lambda _:None):
    from simulation.swarm.experiments import reference_engine, summarize
    request=ExperimentRequest(mode='benchmark',count=30,scenario=scenario,seeds=[seed],duration_s=65,warmup_s=5,range_m=350)
    e=reference_engine(records,config,request,'self_healing_mesh' if policy=='auto' else policy,seed,automatic=policy=='auto')
    initial=e.topology; fleet=[dict(id=n.id,index=n.index,position=n.position[:],target=n.target[:],record=e.records[n.id]) for n in e.nodes.values()]
    fleet_hash=digest(fleet)
    shared=digest(dict(fleet=fleet,seed=seed,scenario=scenario,schedule=schedule(scenario),radio=e.cfg['radio'],
        packets=e.cfg['packets'],fixed_step_s=e.cfg['fixed_step_s'],workload=REFERENCE_WORKLOAD,stop_s=65.,formation=e.formation))
    design=digest({k:v for k,v in e.cfg.items() if k not in ('seed','topology','topology_mode','reference_nominal_demand')})
    path=Path(directory)/f'{scenario}-{policy}-{seed}.jsonl.gz'
    cohort=[]; outcomes=hashlib.sha256(); motion=hashlib.sha256()
    counters=dict(deferred_packets=0,deferred_dropped=0,background_transmissions=0,queue_peak=0,queue_overflows=0)
    deferred=set(); switches=[]; failures=[]; resource=[]; event_cursor=0
    with gzip.open(path,'wt',compresslevel=6) as trace:
        trace.write(json.dumps(dict(kind='study_run_manifest',protocol_id=PROTOCOL_ID,scenario=scenario,policy_id=policy,seed=seed,shared_input_sha256=shared))+'\n')
        def sink(event):
            trace.write(json.dumps(event,separators=(',',':'))+'\n')
            data=event['data']; kind=event['kind']
            canonical=dict(kind=kind,simulation_time=event['simulation_time'],data=data)
            outcomes.update(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()+b'\n')
            if kind=='packet_deferred': counters['deferred_packets']+=1; deferred.add(data['id'])
            if kind=='packet_hop' and data.get('correlation_id')=='reference_background': counters['background_transmissions']+=1
            if kind=='packet_result':
                if data.get('correlation_id')=='reference': cohort.append(data)
                if data.get('reason')=='queue_overflow': counters['queue_overflows']+=1
                if data['id'] in deferred and data['status']=='dropped': counters['deferred_dropped']+=1
        e.trace_sink=sink; e.playing=True; wall=time.perf_counter(); cpu=time.process_time()
        total=round(85/e.cfg['fixed_step_s'])
        for step in range(total):
            if step%20==0 and cancel(): return None
            e.tick(e.cfg['fixed_step_s'])
            motion.update(json.dumps([n.position for n in e.nodes.values()],separators=(',',':')).encode())
            counters['queue_peak']=max(counters['queue_peak'],max(e.scheduler.queued.values(),default=0))
            for event in e.events:
                if event['cursor']<=event_cursor: continue
                if event['kind']=='topology_transition_completed': switches.append(dict(at=event['simulation_time'],**event['data']))
                if event['kind']=='topology_transition_failed': failures.append(dict(at=event['simulation_time'],**event['data']))
                if event['kind'].startswith('topology_') or event['kind'].startswith('condition_'):
                    trace.write(json.dumps(dict(kind=event['kind'],simulation_time=event['simulation_time'],data=event['data']),separators=(',',':'))+'\n')
            event_cursor=e.event_cursor
            if step%20==0:
                # Use the same selector resource proxy for Manual arms, without adapting.
                rates=e.reference_traffic.demands()
                hypotheses=deepcopy(e.adaptive.hypotheses)
                try: c=e.adaptive.candidate(e.topology,e.permitted_physical_links(),rates,{})
                finally: e.adaptive.hypotheses=hypotheses
                resource.append(1-c.factors['efficiency'])
            if step%100==0:
                trace.flush()
                if shutil.disk_usage(directory).free<256*1024**2: raise ValueError('Study stopped before exhausting disk space; completed results retained')
                progress(e.seconds/85)
        if e.scheduler.packets: raise RuntimeError('Packet TTL drain ended with pending packets')
        row=summarize(e,request,e.topology,seed,cohort)
        row.update(protocol_id=PROTOCOL_ID,reference_workload=REFERENCE_WORKLOAD,scenario=scenario,policy_id=policy,
            initial_topology=initial,final_topology=e.topology,fleet_sha256=fleet_hash,initial_input_sha256=shared,design_sha256=design,
            shared_input_sha256=digest(dict(initial=shared,motion=motion.hexdigest(),offers=e.reference_traffic.input_hash.hexdigest())),
            motion_sha256=motion.hexdigest(),offered_input_sha256=e.reference_traffic.input_hash.hexdigest(),outcome_sha256=outcomes.hexdigest(),
            switches=len(switches),switch_events=switches,transition_failures=len(failures),failure_events=failures,
            resource_cost=sum(resource)/len(resource) if resource else None,
            background_offered=e.reference_traffic.background_offered,**counters,
            timing=dict(cpu_s=time.process_time()-cpu,wall_s=time.perf_counter()-wall,diagnostics=e.performance.snapshot(True,1)),
            packet_accounting=dict(generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets)),
            trace_file=path.name)
        return row


def scientific_rows(rows):
    return [{k:v for k,v in r.items() if k!='timing'} for r in rows]


def markdown_report(state):
    p=state['protocol']; rows=state['results']
    lines=['# FLARE Adaptive Resilience Study','',f"Protocol: `{p['id']}` · {p['preset']} · {state['status']}",
        f"Completed runs: {len(rows)} / {p['total_runs']}. Modeled packet simulation; no hardware validation.",'',
        f"Source fingerprint: `{state.get('source_sha256','—')}`",f"Workload: `{REFERENCE_WORKLOAD}`",'',
        '## Primary comparison: Auto − Self-healing Mesh delivery',
        '| Scenario | Valid seed pairs | Mean difference (pp) | 95% bootstrap interval (pp) |',
        '|---|---:|---:|---|']
    for r in state.get('statistics',[]):
        if r['comparison']!='primary': continue
        mean='—' if r['mean_difference'] is None else f"{r['mean_difference']:.3f}"
        interval='Unavailable' if r['interval_95'] is None else ' to '.join(f'{v:.3f}' for v in r['interval_95'])
        lines.append(f"| {r['scenario']} | {r['valid_pairs']} | {mean} | {interval} |")
    lines+=['','## Interpretation and limitations',
        '- Each seed is one replication; 10,000 deterministic paired percentile-bootstrap resamples. Packets stay within runs.',
        '- Secondary comparisons are exploratory. No universal winner is inferred.',
        '- Latency, jitter and hop statistics condition on delivered packets. Recovery times condition on recovered flows; nulls and unrecovered counts are retained.',
        '- Five seconds warmup, sixty measured traffic seconds, twenty seconds TTL drain; 85 simulation seconds total.',
        '- Partition/reconnection is a policy-imposed link cut, not physical movement.',
        '- Nominal reference demand is an estimate before offered mesh observations exist.',
        '- RF, airtime and resource costs are modeled proxies, not calibrated energy or physical network measurements.',
        '- '+LEGACY_LIMITATION+' Historical artifacts are unchanged.',
        '- Wall-clock timings are excluded from scientific result hashes.', '', '## Verification outcomes',
        f"- {'Passed' if state['status']=='completed' and len(rows)==p['total_runs'] else 'Not Tested'}: complete planned run set ({len(rows)}/{p['total_runs']}).",
        f"- {'Passed' if rows and all(r['conservation'] for r in rows) else 'Failed' if rows else 'Not Tested'}: packet conservation in completed runs.",
        '- Not Tested: hardware validation and learned-model quality (excluded from this protocol).']
    if state.get('error'): lines+=['',f"Failed: {state['error']}"]
    if state['status']!='completed': lines+=['','Partial retained results; missing runs are not successful observations.']
    return '\n'.join(lines)+'\n'


def export_study(state,format):
    if format=='json': return json.dumps(state,indent=2,allow_nan=False)
    if format=='md': return markdown_report(state)
    if format=='manifest': return json.dumps({k:v for k,v in state.items() if k not in ('results','statistics')},indent=2)
    if format!='csv': raise ValueError('unsupported export format')
    output=io.StringIO(); writer=csv.DictWriter(output,fieldnames=STUDY_CSV,extrasaction='ignore')
    writer.writeheader(); writer.writerows(state['results']); return output.getvalue()


def _parallel_run(args):
    records,config,scenario,arm,seed,directory=args
    return run_one(records,config,scenario,arm,seed,directory,
        cancel=lambda:(Path(directory)/'cancel.requested').exists())


def study_worker(identity,payload,records,config,directory,commands,workers=1,pilot_sizes=()):
    from simulation.swarm.experiments import source_fingerprint, _atomic_json, SIMULATOR_VERSION
    request=AdaptiveStudyRequest.model_validate(payload); directory=Path(directory)
    p=protocol(request.preset,config['packets']['ttl_s'])
    state=dict(id=identity,mode='adaptive_study',status='running',progress=0.,configuration=payload,protocol=p,
        source_sha256=source_fingerprint(),simulator_version=SIMULATOR_VERSION,reference_workload=REFERENCE_WORKLOAD,
        fleet_records=deepcopy(records),simulation_config=deepcopy(config),results=[],statistics=[],trace_files=[],
        routing_policy=p['reference_policy'],model_inference='not_run_reference_policy',evidence='packet_simulation',
        started_at=time.time(),failed_runs=[],artifact_manifest=[])
    sizes=list(pilot_sizes); filename=directory/'state.json'; executor=None
    def publish(): _atomic_json(filename,state)
    def cancelled():
        try:
            while True:
                if commands.get_nowait()=='cancel': return True
        except Empty: return False
    try:
        for sibling in directory.parent.glob('*/state.json'):
            if sibling==filename: continue
            try: pilot=json.loads(sibling.read_text())
            except (OSError,ValueError): continue
            if pilot.get('source_sha256')==state['source_sha256'] and pilot.get('protocol',{}).get('preset')=='pilot':
                sizes.extend(a['bytes'] for a in pilot.get('artifact_manifest',[]))
        state['storage']=storage_preflight(directory,p['total_runs'],sizes); publish()
        jobs=[(s,seed,a) for s in SCENARIOS for seed in PRESETS[request.preset] for a in ARMS]
        if workers>1:
            # CLI only: API workers remain single-process and cancellable through
            # the existing command queue. Separate processes have no shared RNG.
            from concurrent.futures import ProcessPoolExecutor, as_completed
            import multiprocessing
            executor=ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'))
            pending={executor.submit(_parallel_run,(records,config,s,a,seed,str(directory))):(s,seed,a) for s,seed,a in jobs}
            runs=((pending[f],f) for f in as_completed(pending))
        else: runs=((job,None) for job in jobs)
        for index,((scenario,seed,arm),future) in enumerate(runs):
            if cancelled(): state['status']='cancelled'; break
            state.update(current_scenario=scenario,current_seed=seed,current_policy=arm)
            state['storage']=storage_preflight(directory,len(jobs)-index,sizes)
            def progress(fraction): state.update(progress=(index+fraction)/len(jobs)); publish()
            row=future.result() if future is not None else run_one(records,config,scenario,arm,seed,directory,cancelled,progress)
            if row is None: state['status']='cancelled'; break
            state['results'].append(row); state['trace_files'].append(row['trace_file'])
            trace=directory/row['trace_file']; sizes.append(trace.stat().st_size)
            with trace.open('rb') as stream: trace_hash=hashlib.file_digest(stream,'sha256').hexdigest()
            state['artifact_manifest'].append(dict(file=trace.name,bytes=trace.stat().st_size,sha256=trace_hash))
            state.update(progress=(index+1)/len(jobs)); publish()
        else: state.update(status='completed',progress=1.)
    except Exception as exc:
        state.update(status='failed',error=str(exc))
        state['failed_runs'].append(dict(scenario=state.get('current_scenario'),seed=state.get('current_seed'),policy=state.get('current_policy'),error=str(exc)))
    finally:
        if executor is not None:
            if state['status']!='completed': (directory/'cancel.requested').touch()
            executor.shutdown(wait=True,cancel_futures=True)
        state['results'].sort(key=lambda r:(SCENARIOS.index(r['scenario']),r['seed'],ARMS.index(r['policy_id'])))
        state['incomplete_traces']=[p.name for p in directory.glob('*.jsonl.gz') if p.name not in state['trace_files']]
        try: state['statistics']=statistical_report(state['results'],SCENARIOS,TOPOLOGIES)
        except ValueError as exc: state.update(status='failed',error=str(exc))
        state['results_sha256']=digest(scientific_rows(state['results'])); state['finished_at']=time.time()
        publish()
        for fmt in ('json','csv','md','manifest'):
            (directory/f'results.{fmt}').write_text(export_study(state,fmt))
