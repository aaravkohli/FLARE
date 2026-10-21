"""Bounded real optimizer/checkpoint checks. Run only from a private runtime copy."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
import torch
import yaml

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    if not (ROOT/'audit-source.json').exists():raise RuntimeError('training audit requires a private runtime copy')
    torch.set_num_threads(1);reports=[]
    for algo in ('dqn','sac'):
        for seed in (7,42,99):
            checkpoint=args.output/f'{algo}-{seed}{".zip" if algo=="dqn" else ".pth"}'
            command=[sys.executable,'-m','rl.train','--algo',algo,'--timesteps','1000','--seed',str(seed),'--output-path',str(checkpoint),'--evaluation-episodes','3','--evaluation-frequency','1000','--no-tensorboard']
            row={'capability':f'{algo} training/checkpoint/evaluation','seed':seed,'command':command,'status':'Not Tested'};start=time.monotonic()
            try:
                with (args.output/f'{algo}-{seed}.log').open('w') as log:
                    result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=240)
                assert result.returncode==0,f'training exit {result.returncode}'
                if algo=='dqn':
                    from stable_baselines3 import DQN
                    model=DQN.load(checkpoint);updates=model._n_updates
                    predict=lambda value:model.predict(value,deterministic=True)[0]
                else:
                    from rl.sac_discrete import DiscreteSACAgent
                    model=DiscreteSACAgent(state_dim=14,action_dim=3);model.load(str(checkpoint))
                    saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
                    updates=max(int(v['step']) for v in saved['critic_opt']['state'].values())
                    predict=lambda value:model.predict(value,deterministic=True)[0]
                assert updates>0,'no optimizer update'
                from rl.env import DronePathEnv
                rewards=[]
                for index in range(3):
                    env=DronePathEnv();obs,_=env.reset(seed=seed+10000+index);reward_sum=0
                    for _ in range(600):
                        action=int(predict(obs));assert 0<=action<3
                        obs,reward,done,truncated,_=env.step(action);reward_sum+=float(reward)
                        if done or truncated:break
                    assert np.isfinite(reward_sum);rewards.append(reward_sum)
                assert Path(str(checkpoint)+'.metadata.json').exists()
                row.update(status='Passed',optimizer_updates=updates,evaluation_rewards=rewards)
            except Exception as exc:row.update(status='Failed',error=f'{type(exc).__name__}: {exc}')
            row['duration_s']=time.monotonic()-start;reports.append(row);(args.output/'results.json').write_text(json.dumps(reports,indent=2));print(json.dumps(row),flush=True)
    print('Training checks finished',flush=True)

if __name__=='__main__':main()
