"""Isolated CPU-cost comparison with a manual baseline using the same initial graph."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
import time
import yaml

from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import source_fingerprint


def profile(count=100, seconds=10, seed=42):
    root=Path(__file__).resolve().parents[1]
    cfg=yaml.safe_load((root/'config/swarm_config.yaml').read_text())
    cfg.update(default_count=count,seed=seed,topology_mode='auto')
    initial=SwarmEngine([],cfg,isolated=True).topology
    runs=[]
    for mode in ('manual','auto'):
        config=deepcopy(cfg);config.update(topology_mode=mode,topology=initial)
        e=SwarmEngine([],config,isolated=True)
        original=e.adaptive.evaluate;measurements=[]
        def measured(force=False):
            previous=e.adaptive.last_evaluation;start=time.process_time()
            result=original(force)
            if e.adaptive.last_evaluation!=previous:measurements.append(time.process_time()-start)
            return result
        e.adaptive.evaluate=measured;e.playing=True
        wall=time.perf_counter();cpu=time.process_time()
        for _ in range(round(seconds/config['fixed_step_s'])):e.tick(config['fixed_step_s'])
        runs.append(dict(mode=mode,initial_topology=initial,active_topology=e.topology,
                         simulated_seconds=e.seconds,cpu_seconds=time.process_time()-cpu,
                         wall_seconds=time.perf_counter()-wall,evaluations=len(measurements),
                         evaluation_cpu_ms=[round(x*1000,3) for x in measurements],
                         selector_cpu_seconds=sum(measurements),
                         conservation=e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)))
    return dict(source_sha256=source_fingerprint(),count=count,seed=seed,hardware=platform.platform(),
                process_clock='time.process_time',note='Modeled packet simulation; not a real-time deadline or hardware network benchmark',runs=runs,
                total_cpu_ratio=runs[1]['cpu_seconds']/runs[0]['cpu_seconds'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,choices=range(10,101,10),default=100)
    parser.add_argument('--seconds',type=int,choices=range(1,61),default=10)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=profile(args.count,args.seconds,args.seed)
    output=json.dumps(result,indent=2)
    if args.output:args.output.write_text(output+'\n')
    print(output)
