"""Fresh-process matched 100-UAV autonomy overhead; modeled batch speed, not renderer FPS."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import time
import yaml
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import source_fingerprint


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--seconds',type=int,default=10);a=p.parse_args();a.directory.mkdir(parents=True,exist_ok=False)
    source=source_fingerprint();base=yaml.safe_load(Path('config/swarm_config.yaml').read_text());rows=[]
    for seed in (7,42,99):
        initial=None
        for mode in ('off','recovery_v1'):
            cfg=deepcopy(base);cfg.update(default_count=100,seed=seed,topology_mode='auto',autonomy_mode=mode,logical_channel_keys=True)
            start=time.process_time();e=SwarmEngine([],cfg,isolated=True);startup=time.process_time()-start
            positions=hashlib.sha256(json.dumps([d.position for d in e.nodes.values()],sort_keys=True).encode()).hexdigest()
            if initial:assert positions==initial
            initial=positions;e.playing=True;cpu=time.process_time();wall=time.perf_counter()
            for _ in range(round(a.seconds/.05)):e.tick(.05)
            elapsed=time.perf_counter()-wall;cpu=time.process_time()-cpu
            row=dict(seed=seed,autonomy=mode,positions_sha256=positions,startup_cpu_s=startup,cpu_s=cpu,wall_s=elapsed,simulation_s=e.seconds,maximum_batch_speed=e.seconds/elapsed,generated=e.scheduler.generated,pending=len(e.scheduler.packets),control_traffic=dict(e.autonomy.counters),timings=e.performance.snapshot(True,1)['timings'])
            rows.append(row);print(seed,mode,round(cpu,3),flush=True)
            (a.directory/'profile.json').write_text(json.dumps(dict(source_sha256=source,hardware=platform.uname()._asdict(),count=100,description='Sequential isolated 10-sim-second batch runs, three seeds; no model inference or real-time pacing. Added control traffic and resulting topology choices are endogenous costs. CPU timings are excluded from scientific hashes.',results=rows),indent=2))
    if source_fingerprint()!=source:raise RuntimeError('Source changed during profiling')

if __name__=='__main__':main()
