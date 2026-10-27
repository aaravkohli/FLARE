"""Exploratory 72-run ablation. Historical adaptive_resilience_v1 is untouched."""
from copy import deepcopy
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
from queue import Empty
import time

from schemas.mission import MissionCommand
from simulation.swarm.adaptive_study import digest, storage_preflight
from simulation.swarm.reference_workload import install, REFERENCE_WORKLOAD
from simulation.swarm.scenarios import schedule
from simulation.swarm.study_statistics import paired_summary
from simulation.swarm.resilience_analysis import EvidenceRecovery

PROTOCOL_ID='evidence_resilience_pilot_v1'
SCENARIOS=('normal','mobility','congestion','regional_jamming','single_failure','partition_reconnection')
ARMS=('balanced_control','packet_transition','observability_guard','combined')
SEEDS=(7,42,99)
METRICS=('pdr','average_latency_ms','jitter_ms','throughput_mbps','average_hops','connected_components','route_convergence_ms','service_recovery_ms','unrecovered_flows',
    'switches','transition_failures','protected_failures','deadline_misses','command_ack_ratio','ack_latency_ms',
    'fresh_evidence_ratio','evidence_recovery_ms','report_delivery_ratio','transition_dropped','protected_airtime_s')


def protocol(ttl_s=20.):
    return dict(id=PROTOCOL_ID,preset='pilot',exploratory=True,count=30,seeds=list(SEEDS),scenarios=list(SCENARIOS),
        policies=list(ARMS),total_runs=72,warmup_s=5.,measured_traffic_s=60.,traffic_stop_s=65.,ttl_drain_s=float(ttl_s),
        total_simulation_s=65.+float(ttl_s),reference_workload=REFERENCE_WORKLOAD,model_inference='excluded',
        observation_transport='registered mesh reports in all arms; 10% protected service only in observability and combined arms',
        comparison='Combined minus each ablation; exploratory seed-level paired intervals',
        command_schedule='role=standard to one round-robin UAV at seconds 6,11,...,61; TTL 10 seconds',
        report_interval_s=1.,freshness_s=3.,partition_mechanism='policy-imposed link cut',
        feature_arms={a:dict(packet_transitions=a in ('packet_transition','combined'),observation_guard=a in ('observability_guard','combined'),protected_scheduling=a in ('observability_guard','combined')) for a in ARMS})


def summaries(rows):
    converted=[dict(r,policy_id='auto' if r['policy_id']=='combined' else r['policy_id']) for r in rows]
    return [dict(paired_summary(converted,s,b,m),comparison='exploratory')
            for s in SCENARIOS for b in ARMS[:-1] for m in METRICS]


def run_one(records,config,scenario,arm,seed,directory,cancel=lambda:False,progress=lambda _:None):
    from simulation.swarm.engine import SwarmEngine
    from simulation.swarm.experiments import summarize
    from schemas.mission import ExperimentRequest
    cfg=deepcopy(config); design=protocol(cfg['packets']['ttl_s']);flags=design['feature_arms'][arm]
    cfg.pop('autonomy_mode',None);cfg.pop('safety_profile',None);cfg.pop('affected_uav_assist',None)
    cfg.update(default_count=30,seed=seed,topology='self_healing_mesh',topology_mode='auto',formation='distributed',
        adaptive_policy='evidence_resilience_v1',mesh_only=True,observation_base_endpoint=True,logical_channel_keys=True,
        reference_nominal_demand=True,traffic_stop_s=65.,observation_stop_s=65.)
    cfg['resilience']=dict(cfg.get('resilience',{}),**flags)
    e=SwarmEngine(records,cfg,isolated=True);e.scenario=scenario;install(e,65.)
    initial=e.topology; fleet=[dict(id=n.id,index=n.index,position=n.position[:],record=e.records[n.id]) for n in e.nodes.values()]
    design_cfg=deepcopy(cfg);design_cfg['resilience'].pop('packet_transitions',None);design_cfg['resilience'].pop('observation_guard',None);design_cfg['resilience'].pop('protected_scheduling',None);design_cfg.pop('seed')
    shared=digest(dict(fleet=fleet,seed=seed,scenario=scenario,schedule=schedule(scenario),config=design_cfg,protocol=design))
    trace_file=f'{scenario}-{arm}-{seed}.jsonl.gz'; cohort=[]; counters=Counter(deferred_packets=0,deferred_dropped=0); deferred=set(); motion=hashlib.sha256(); outcomes=hashlib.sha256()
    report_inputs=hashlib.sha256();commands_inputs=hashlib.sha256(); transitions=[]; cursor=0; coverage=[]; report_ages=[]; recovery=EvidenceRecovery(scenario)
    with gzip.open(Path(directory)/trace_file,'wt',compresslevel=6) as trace:
        trace.write(json.dumps(dict(kind='run_manifest',protocol_id=PROTOCOL_ID,policy_id=arm,seed=seed,scenario=scenario,shared_input_sha256=shared))+'\n')
        def sink(event):
            data=event['data'];kind=event['kind']
            canonical=dict(kind=kind,simulation_time=event['simulation_time'],data=data)
            encoded=json.dumps(canonical,sort_keys=True,separators=(',',':'))
            trace.write(encoded+'\n');outcomes.update(encoded.encode()+b'\n')
            if kind=='packet_deferred':counters['deferred_packets']+=1;deferred.add(data['id'])
            if kind=='packet_queued' and data.get('service_kind')=='security_report':
                # Payload observations and therefore serialized lengths may depend on
                # prior outcomes. Capture schedule/identity is the shared input.
                report_inputs.update(json.dumps([data['source'],data['created_at'],data['channel_key'].rsplit(':',1)[0]],separators=(',',':')).encode())
            if kind=='packet_result':
                if data.get('correlation_id')=='reference':cohort.append(data)
                if data.get('service_kind'):
                    counters['protected_completed']+=1
                    counters['protected_failures']+=int(data['status']!='delivered')
                    counters['deadline_misses']+=int(data['completed_at']>=data['service_deadline']-1e-9)
                if data.get('reason')=='queue_overflow':counters['queue_overflows']+=1
                if data['id'] in deferred and data['status']=='dropped':counters['deferred_dropped']+=1
        e.trace_sink=sink;e.playing=True; cpu=time.process_time();wall=time.perf_counter();command_index=0
        for step in range(round(design['total_simulation_s']/.05)):
            if step%20==0 and cancel():return None
            if command_index<12 and e.seconds+1e-9>=6+5*command_index:
                identity=list(e.nodes)[command_index%30];command_id=f'pilot:{seed}:{command_index}'
                commands_inputs.update(json.dumps([6+5*command_index,identity,'role','standard',10],separators=(',',':')).encode())
                e.command(MissionCommand(session_id=e.session_id,expected_revision=e.revision,command_id=command_id,targets=[identity],action='role',parameters={'role':'standard'},ttl_s=10));command_index+=1
            e.tick(.05)
            motion.update(json.dumps([n.position for n in e.nodes.values()],separators=(',',':')).encode())
            if 5<=e.seconds<65:
                statuses=e.observability.statuses();eligible=[r for r in statuses.values() if r['eligible']]
                ratio=sum(r['status']=='Fresh' for r in eligible)/len(eligible) if eligible else None
                if ratio is not None:coverage.append(ratio)
                report_ages.extend(r['age_s'] for r in eligible if r['age_s'] is not None)
                recovery.observe(e.seconds,ratio)
            for event in e.events:
                if event['cursor']<=cursor:continue
                if event['kind'].startswith('topology_'):
                    transitions.append(dict(kind=event['kind'],simulation_time=event['simulation_time'],data=event['data']))
                    trace.write(json.dumps(transitions[-1],separators=(',',':'))+'\n')
            cursor=e.event_cursor
            if step%100==0:trace.flush();storage_preflight(directory,1,[1024**2]);progress(e.seconds/design['total_simulation_s'])
        if e.scheduler.packets:raise RuntimeError('TTL drain left pending packets')
        req=ExperimentRequest(mode='benchmark',count=30,duration_s=65,warmup_s=5,scenario=scenario,seeds=[seed])
        row=summarize(e,req,e.topology,seed,cohort)
        command_outcomes=[outcome for c in e.commands.values() for outcome in c['outcomes'].values()]
        latencies=[(o['acknowledged_at']-o['accepted_at'])*1000 for o in command_outcomes if o.get('acknowledged_at') is not None]
        obs=e.observability.counters
        row.update(protocol_id=PROTOCOL_ID,reference_workload=REFERENCE_WORKLOAD,scenario=scenario,policy_id=arm,
            initial_topology=initial,final_topology=e.topology,fleet_sha256=digest(fleet),design_sha256=digest(design_cfg),
            shared_input_sha256=digest(dict(initial=shared,motion=motion.hexdigest(),primary=e.reference_traffic.input_hash.hexdigest(),commands=commands_inputs.hexdigest())),
            initial_input_sha256=shared,motion_sha256=motion.hexdigest(),offered_input_sha256=e.reference_traffic.input_hash.hexdigest(),
            command_input_sha256=commands_inputs.hexdigest(),report_schedule_sha256=report_inputs.hexdigest(),outcome_sha256=outcomes.hexdigest(),
            switches=sum(t['kind']=='topology_transition_completed' for t in transitions),
            transition_failures=sum(t['kind']=='topology_transition_failed' for t in transitions),switch_events=transitions,
            transition_dropped=sum(p['realized']['dropped'] for p in e.resilience.history),
            transition_plans=list(e.resilience.history),**counters,
            command_ack_ratio=len(latencies)/len(command_outcomes) if command_outcomes else None,
            ack_latency_ms=sum(latencies)/len(latencies) if latencies else None,
            fresh_evidence_ratio=sum(coverage)/len(coverage) if coverage else None,
            evidence_recovery_ms=recovery.value,evidence_unrecovered=recovery.unrecovered,
            mean_report_age_s=sum(report_ages)/len(report_ages) if report_ages else None,
            report_delivery_ratio=obs['delivered']/obs['offered'] if obs['offered'] else None,
            report_counts=dict(obs),protected_airtime_s=e.scheduler.airtime_totals['protected'],
            airtime=dict(e.scheduler.airtime_totals),background_offered=e.reference_traffic.background_offered,
            resource_cost=None,
            packet_accounting=dict(generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets)),
            timing=dict(cpu_s=time.process_time()-cpu,wall_s=time.perf_counter()-wall),trace_file=trace_file)
        return row


def report(state):
    lines=['# FLARE Evidence-Aware Resilience Pilot','',f"{PROTOCOL_ID} · {state['status']} · {len(state['results'])}/72 runs",'',
        'Exploratory modeled packet simulation. No patentability, RF hardware or learned-detector quality claim.',
        'All arms share delivered-report capture schedules. Packet transitions and observability protection (candidate guards plus 10% protected scheduling) are ablated.',
        f"5s warmup + 60s measured traffic + {state.get('protocol',{}).get('ttl_drain_s',20):g}s TTL drain. Reports end at 65s. No evidence seeds were used.",
        'Report payloads reflect each arm\'s observations; their realized bytes are endogenous, not a shared external input.',
        'Ten-thousand paired seed-level bootstrap resamples. Three seeds do not establish general superiority.',
        'Latency conditions on delivery; acknowledgment latency on received acknowledgments; evidence recovery on recovery.',
        '', '| Scenario | Baseline | Metric | Pairs | Combined − baseline | 95% interval |','|---|---|---|---:|---:|---|']
    for r in state.get('statistics',[]):
        if r['metric'] not in ('pdr','fresh_evidence_ratio','command_ack_ratio'):continue
        lines.append(f"| {r['scenario']} | {r['baseline']} | {r['metric']} | {r['valid_pairs']} | {r['mean_difference']} | {r['interval_95']} |")
    if state.get('error'):lines+=['',f"Failed: {state['error']}"]
    return '\n'.join(lines)+'\n'


def export_study(state,format):
    if format=='json':return json.dumps(state,indent=2,allow_nan=False)
    if format=='md':return report(state)
    if format=='manifest':return json.dumps({k:v for k,v in state.items() if k not in ('results','statistics')},indent=2)
    if format!='csv':raise ValueError('unsupported export format')
    stream=io.StringIO();fields=['scenario','policy_id','seed',*METRICS,'generated','delivered','dropped','conservation','shared_input_sha256','outcome_sha256']
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(state['results']);return stream.getvalue()


def study_worker(identity,payload,records,config,directory,commands):
    from simulation.swarm.experiments import source_fingerprint,_atomic_json,SIMULATOR_VERSION
    directory=Path(directory);state=dict(id=identity,mode='adaptive_study',configuration=payload,protocol=protocol(config['packets']['ttl_s']),status='running',progress=0.,
        source_sha256=source_fingerprint(),simulator_version=SIMULATOR_VERSION,simulation_config=deepcopy(config),fleet_records=deepcopy(records),
        results=[],statistics=[],trace_files=[],artifact_manifest=[],failed_runs=[],evidence='packet_simulation',started_at=time.time())
    def publish():_atomic_json(directory/'state.json',state)
    def cancel():
        try:
            while True:
                if commands.get_nowait()=='cancel':return True
        except Empty:return False
    try:
        storage_preflight(directory,72);publish()
        jobs=[(s,seed,a) for s in SCENARIOS for seed in SEEDS for a in ARMS]
        for index,(scenario,seed,arm) in enumerate(jobs):
            state.update(current_scenario=scenario,current_seed=seed,current_policy=arm)
            def progress(fraction):state['progress']=(index+fraction)/72;publish()
            row=run_one(records,config,scenario,arm,seed,directory,cancel,progress)
            if row is None:state['status']='cancelled';break
            state['results'].append(row);state['trace_files'].append(row['trace_file'])
            trace=directory/row['trace_file']
            state['artifact_manifest'].append(dict(file=trace.name,bytes=trace.stat().st_size,sha256=hashlib.sha256(trace.read_bytes()).hexdigest()))
            state['progress']=(index+1)/72;publish()
        else:state['status']='completed';state['progress']=1.
    except Exception as exc:
        state.update(status='failed',error=str(exc));state['failed_runs'].append(dict(scenario=state.get('current_scenario'),seed=state.get('current_seed'),policy=state.get('current_policy'),error=str(exc)))
    finally:
        try:state['statistics']=summaries(state['results'])
        except ValueError as exc:state.update(status='failed',error=str(exc))
        state['incomplete_traces']=[p.name for p in directory.glob('*.jsonl.gz') if p.name not in state['trace_files']]
        state['results_sha256']=digest([{k:v for k,v in r.items() if k!='timing'} for r in state['results']]);state['finished_at']=time.time()
        publish()
        for fmt in ('json','csv','md','manifest'):(directory/f'results.{fmt}').write_text(export_study(state,fmt))
