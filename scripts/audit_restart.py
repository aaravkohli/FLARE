"""Actual disposable simulator shutdown/restart and completed-job persistence."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import requests
from scripts.audit_support import runtime_copy,protected_files
from simulation.swarm.session import bootstrap


def main():
 root=Path(__file__).resolve().parents[1];before=protected_files(root);folder=Path(sys.argv[1]);folder.mkdir(parents=True);copy=runtime_copy(root,folder/'project');bootstrap(copy/'config/fleet_registry.yaml',folder/'registry.yaml');env=dict(os.environ,FLARE_SWARM_ENABLED='1',FLARE_FLEET_REGISTRY=str(folder/'registry.yaml'),FLARE_EXPERIMENT_DIR=str(folder/'experiments'),FLARE_BYZANTINE_STATE_PATH=str(folder/'attacks.json'));process=None;rows=[]
 def launch(number):
  nonlocal process
  with (folder/f'simulation-{number}.log').open('w') as log:process=subprocess.Popen([sys.executable,'-m','uvicorn','sdn.mock_sdn:app','--port','18880'],cwd=copy,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  deadline=time.monotonic()+30
  while time.monotonic()<deadline:
   try:
    if call('GET','/state'):return
   except requests.RequestException:pass
   time.sleep(.1)
  raise AssertionError('Simulator startup timed out')
 def stop():
  if process is not None and process.poll() is None:os.killpg(process.pid,signal.SIGTERM);process.wait(10)
 def call(method,path,body=None,status=200):
  response=requests.request(method,'http://127.0.0.1:18880/simulation'+path,json=body,headers={'Authorization':'Bearer antijam-development-sdn-token'},timeout=10);assert response.status_code==status,(path,response.status_code,response.text[:300]);return response.json()
 try:
  launch(1);initial=call('GET','/state');current=call('PATCH','/config',dict(expected_revision=initial['revision'],count=10,topology='ring'))
  job=call('POST','/experiments',dict(mode='benchmark',topologies=['star','ring'],count=10,seeds=[7],duration_s=10,warmup_s=0))
  deadline=time.monotonic()+60
  while time.monotonic()<deadline:
   result=call('GET','/experiments/'+job['id'])
   if result['status']=='completed':break
   time.sleep(.2)
  assert result['status']=='completed'
  current=call('GET','/state');queued=call('POST','/commands',dict(command_id='prior-session',session_id=current['session_id'],expected_revision=current['revision'],targets=[current['nodes'][0]['id']],action='hold'))
  assert list(queued['outcomes'].values())[0]['status']=='queued'
  current=call('GET','/state');current=call('POST','/control',dict(action='play',expected_revision=current['revision']));time.sleep(.4);playing=call('GET','/state');assert playing['simulation_time']>0
  stop();launch(2);restarted=call('GET','/state');assert restarted['session_id']!=initial['session_id'];assert restarted['simulation_time']==0 and not restarted['playing'];assert restarted['commands']==[]
  missing=call('GET','/commands/prior-session',status=404);retained=call('GET','/experiments/'+job['id']);assert retained['status']=='completed' and retained['results_sha256']==result['results_sha256']
  rows.append(dict(capability='actual process restart and completed experiment persistence',status='Passed',before_session=initial['session_id'],after_session=restarted['session_id'],completed_experiment=job['id'],results_sha256=retained['results_sha256'],prior_command_status=404,evidence='Actual HTTP service, graceful SIGTERM and new process'))
 except Exception as exc:rows.append(dict(capability='actual process restart and persistence',status='Failed',error=f'{type(exc).__name__}: {exc}'))
 finally:stop()
 assert protected_files(root)==before
 (folder/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows),flush=True)

if __name__=='__main__':main()
