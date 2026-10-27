"""Compare isolated pre/post source trees without changing simulation inputs.

Each timing trial uses a fresh process. Packet event and sampled authoritative
snapshot digests must match before a measured cost reduction can be reported.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


def canonical(value):
    if isinstance(value, dict):
        return {k:canonical(v) for k,v in value.items()
                if k not in ('session_id','timestamp','wall_captured_at','performance','runtime')}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return value


def child(args):
    from copy import deepcopy
    import yaml
    from simulation.swarm.engine import SwarmEngine
    from simulation.swarm.experiments import source_fingerprint
    from simulation.swarm.reference_workload import install
    cfg = yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg = deepcopy(cfg)
    cfg.update(default_count=100,seed=args.seed,topology_mode='auto',
               adaptive_policy='evidence_resilience_v1',logical_channel_keys=True)
    if args.workload == 'reference':
        cfg.update(mesh_only=True,observation_base_endpoint=True,reference_nominal_demand=True,
                   observation_stop_s=args.seconds,traffic_stop_s=args.seconds)
    started = time.process_time()
    e = SwarmEngine([],cfg,isolated=True)
    startup = time.process_time()-started
    if args.workload == 'reference': install(e,args.seconds)
    packets = hashlib.sha256();snapshots = hashlib.sha256();event_count = 0
    def sink(event):
        nonlocal event_count
        # Packet payloads include exact hop timestamps, identities and outcomes.
        data = {k:v for k,v in event.items() if k != 'session_id'}
        packets.update(json.dumps(data,sort_keys=True,separators=(',',':')).encode()+b'\n')
        event_count += 1
    e.trace_sink = sink;e.playing = True
    start = time.process_time();wall = time.perf_counter()
    for step in range(round(args.seconds/.05)):
        e.tick(.05)
        if step%4 == 0:
            snapshots.update(json.dumps(canonical(e.snapshot()),sort_keys=True,separators=(',',':')).encode()+b'\n')
    elapsed = time.perf_counter()-wall;cpu = time.process_time()-start
    print(json.dumps(dict(workload=args.workload,seed=args.seed,count=100,
          simulation_seconds=e.seconds,cpu_s=cpu,wall_s=elapsed,startup_cpu_s=startup,
          packet_event_count=event_count,packet_sha256=packets.hexdigest(),
          snapshot_sha256=snapshots.hexdigest(),source_sha256=source_fingerprint(),
          topology=e.topology,generated=e.scheduler.generated,delivered=e.scheduler.delivered,
          dropped=e.scheduler.dropped,pending=len(e.scheduler.packets))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--seconds',type=float,default=5.)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--child',action='store_true')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--workload',choices=('live','reference'),default='live')
    args = p.parse_args()
    if args.seconds <= 0 or args.repeats < 1: p.error('positive seconds and repeats required')
    if args.child: return child(args)
    if not args.directory or not args.baseline: p.error('--directory and --baseline are required')
    args.directory.mkdir(parents=True,exist_ok=False)
    roots = dict(baseline=args.baseline.resolve(),optimized=Path.cwd())
    script = str(Path(__file__).resolve());rows=[]
    for workload in ('live','reference'):
        for seed in (7,42,99):
            for repetition in range(args.repeats):
                # Alternate order to avoid confounding one tree with warm hardware.
                for arm in (('baseline','optimized') if repetition%2 == 0 else ('optimized','baseline')):
                    root = roots[arm]
                    command = [sys.executable,script,'--child','--workload',workload,'--seed',str(seed),'--seconds',str(args.seconds)]
                    result = subprocess.run(command,cwd=root,env=dict(os.environ,PYTHONPATH=str(root),PYTHONHASHSEED='7'),check=True,capture_output=True,text=True)
                    row = json.loads(result.stdout);row.update(arm=arm,repetition=repetition)
                    rows.append(row)
                    (args.directory/'trials.json').write_text(json.dumps(rows,indent=2))
                    print(workload,seed,repetition,arm,round(row['cpu_s'],3),flush=True)
    comparisons=[]
    for workload in ('live','reference'):
        for seed in (7,42,99):
            pairs=[]
            for repetition in range(args.repeats):
                pair=[next(r for r in rows if (r['workload'],r['seed'],r['repetition'],r['arm'])==(workload,seed,repetition,a)) for a in ('baseline','optimized')]
                fields=('packet_event_count','packet_sha256','snapshot_sha256','topology','generated','delivered','dropped','pending')
                mismatches=[k for k in fields if pair[0][k]!=pair[1][k]]
                if mismatches:raise RuntimeError(f'Changed scientific output: {workload} {seed} {repetition} {mismatches}')
                pairs.append(pair)
            baseline=sum(a['cpu_s'] for a,b in pairs)/len(pairs)
            optimized=sum(b['cpu_s'] for a,b in pairs)/len(pairs)
            comparisons.append(dict(workload=workload,seed=seed,repeats=len(pairs),identical=True,
                 baseline_cpu_s=baseline,optimized_cpu_s=optimized,cpu_reduction_fraction=1-optimized/baseline))
    output=dict(outcome='Passed',description='Fresh-process 100-UAV safeguarded workloads, snapshots every 0.2 simulation seconds. Exact packet data; snapshot digests exclude wall/session/runtime fields only.',
                hardware=platform.uname()._asdict(),seconds=args.seconds,comparisons=comparisons,rows=rows)
    (args.directory/'comparison.json').write_text(json.dumps(output,indent=2))
    print(json.dumps(comparisons,indent=2))


if __name__ == '__main__':main()
