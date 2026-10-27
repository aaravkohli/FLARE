"""Matched 100-UAV Auto/Manual CPU observations; no real-time speed claim."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import time
import yaml
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import source_fingerprint
from simulation.swarm.reference_workload import install


def main():
    config=yaml.safe_load(Path('config/swarm_config.yaml').read_text());results=[]
    for seed in (7,42,99):
        selected=None;positions=None
        for mode in ('auto','manual'):
            cfg=deepcopy(config)
            cfg.update(default_count=100,seed=seed,topology_mode=mode,topology=selected or 'self_healing_mesh',
                adaptive_policy='evidence_resilience_v1',mesh_only=True,observation_base_endpoint=True,
                logical_channel_keys=True,reference_nominal_demand=True,observation_stop_s=10.,traffic_stop_s=10.)
            start=time.process_time();engine=SwarmEngine([],cfg,isolated=True);startup=time.process_time()-start
            selected=engine.topology
            initial=json.dumps([n.position for n in engine.nodes.values()],separators=(',',':'))
            if positions is not None:assert initial==positions
            positions=initial;install(engine,10.);engine.playing=True
            start=time.process_time();wall=time.perf_counter()
            for _ in range(200):engine.tick(.05)
            cpu=time.process_time()-start;elapsed=time.perf_counter()-wall
            results.append(dict(seed=seed,mode=mode,initial_topology=selected,final_topology=engine.topology,
                simulated_s=engine.seconds,cpu_s=cpu,wall_s=elapsed,startup_cpu_s=startup,
                positions_sha256=hashlib.sha256(initial.encode()).hexdigest(),
                timings=engine.performance.snapshot(True,1)['timings'],
                generated=engine.scheduler.generated,pending=len(engine.scheduler.packets),
                report_counts=dict(engine.observability.counters)))
            print(json.dumps(results[-1]),flush=True)
    output=dict(source_sha256=source_fingerprint(),hardware=platform.uname()._asdict(),count=100,
        description='Both arms use receipt-backed telemetry and protected scheduling. Manual pins the initial Auto topology; no inference or wall-clock throttling. Different subsequent topology decisions are allowed.',results=results)
    Path('results/evidence_resilience/cpu-profile.json').write_text(json.dumps(output,indent=2))


if __name__=='__main__':main()
