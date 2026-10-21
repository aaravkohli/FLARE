"""Bounded CLI/research checks in a dedicated private source tree."""
import json
from pathlib import Path
import subprocess
import sys
import time
from scripts.audit_support import runtime_copy,protected_files


def main():
 output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True);root=Path(__file__).resolve().parents[1];before=protected_files(root);copy=runtime_copy(root,output/'project');rows=[]
 commands=[('byzantine', ['-m','scripts.run_byzantine_experiment','--rounds','3','--seeds','7','42','99']),('insider',['-m','scripts.run_insider_experiment','--rounds','3','--seeds','7','42','99']),('network-security',['-m','scripts.run_network_security_experiment','--samples-per-profile','10','--seeds','7','42','99']),('non-iid-ablation',['-m','scripts.run_fl_resilience_matrix','--rounds','1','--seeds','7','42','99'])]
 for seed in [7,42,99]:commands.append((f'routing-v3-{seed}',['-m','scripts.train_routing_v3','--timesteps','1000','--seed',str(seed),'--episodes','3','--output',str(output/f'v3-{seed}.zip')]))
 for name,args in commands:
  command=[sys.executable,*args];started=time.monotonic();row=dict(capability=name,command=command,status='Not Tested')
  try:
   with (output/(name+'.log')).open('w') as log:result=subprocess.run(command,cwd=copy,stdout=log,stderr=subprocess.STDOUT,timeout=180)
   row['exit_code']=result.returncode;assert result.returncode==0
   if name.startswith('routing-v3'):
    from stable_baselines3 import DQN
    model=DQN.load(output/f'v3-{name.rsplit("-",1)[-1]}.zip');assert model._n_updates>0;assert model.observation_space.shape==(18,);assert model.action_space.n==4;row['optimizer_updates']=model._n_updates
   row['status']='Passed'
  except Exception as exc:row.update(status='Failed',error=f'{type(exc).__name__}: {exc}')
  row['duration_s']=time.monotonic()-started;rows.append(row);(output/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
 assert protected_files(root)==before

if __name__=='__main__':main()
