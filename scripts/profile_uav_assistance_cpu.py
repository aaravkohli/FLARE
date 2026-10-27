"""Matched isolated 100-UAV assistance CPU cost, separate from rendering FPS."""
import argparse,json,platform,time
from pathlib import Path
from copy import deepcopy
import yaml
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import source_fingerprint

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--seconds',type=int,default=10);a=p.parse_args();a.directory.mkdir(exist_ok=False,parents=True)
    base=yaml.safe_load(Path('config/swarm_config.yaml').read_text());rows=[];source=source_fingerprint()
    for policy in ('balanced_v1','evidence_resilience_v1'):
        for seed in (7,42,99):
            for enabled in (False,True):
                cfg=deepcopy(base);cfg.update(default_count=100,seed=seed,affected_uav_assist=enabled,adaptive_policy=policy,autonomy_mode='off',logical_channel_keys=True)
                start=time.process_time();e=SwarmEngine([],cfg,isolated=True);startup=time.process_time()-start;e.playing=True;cpu=time.process_time();wall=time.perf_counter()
                for _ in range(round(a.seconds/.05)):e.tick(.05)
                row=dict(policy=policy,seed=seed,assistance=enabled,startup_cpu_s=startup,cpu_s=time.process_time()-cpu,wall_s=time.perf_counter()-wall,simulation_s=e.seconds,generated=e.scheduler.generated,delivered=e.scheduler.delivered,dropped=e.scheduler.dropped,pending=len(e.scheduler.packets),timings=e.performance.snapshot(True,1)['timings'],assistance_counters=dict(e.assistance.counters))
                rows.append(row);print(policy,seed,enabled,row['cpu_s'],flush=True)
                (a.directory/'profile.json').write_text(json.dumps(dict(source_sha256=source,hardware=platform.uname()._asdict(),count=100,seconds=a.seconds,results=rows,note='Sequential fresh engine runs. process_time measures CPU; wall-clock batch speed is not real-time achieved speed or renderer FPS. No model inference. Primary demand and seed matched; probes/topology changes are endogenous.'),indent=2))
    assert source_fingerprint()==source
if __name__=='__main__':main()
