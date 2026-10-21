"""Bounded service suites against disposable copies, never operator services."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import requests
import yaml
from scripts.audit_support import runtime_copy,protected_files
from simulation.swarm.session import bootstrap


def main():
 p=argparse.ArgumentParser();p.add_argument('suite',choices=['legacy','federation','selftests']);p.add_argument('--output',type=Path,required=True);p.add_argument('--cases',nargs='+',default=['normal','jammed','poisoned']);p.add_argument('--transport',choices=['mesh','satellite'],default='mesh');p.add_argument('--advanced',action='store_true');p.add_argument('--seed',type=int,default=42);a=p.parse_args()
 root=Path(__file__).resolve().parents[1];before=protected_files(root);a.output.mkdir(parents=True,exist_ok=True);rows=[]
 def save(row):
  rows.append(row);(a.output/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
 for index,case in enumerate(a.cases if a.suite=='federation' else [a.suite]):
  folder=a.output/case;folder.mkdir();copy=runtime_copy(root,folder/'project');bootstrap(copy/'config/fleet_registry.yaml',folder/'mission-registry.yaml');env=os.environ.copy();env.update(OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MODE='simulation',DATABASE_PATH=str(folder/'decisions.db'),FLARE_FLEET_REGISTRY=str(folder/'mission-registry.yaml'),FLARE_SWARM_ENABLED='1' if a.suite=='federation' else '0',FLARE_BYZANTINE_STATE_PATH=str(folder/'byzantine.json'),FLARE_FL_POOL_PLAN=str(folder/'pool.json'),FLARE_CLIENT_MANAGER_STATUS=str(folder/'clients.json'),FLARE_CLIENT_STATE_DIR=str(folder/'client-state'),FLARE_PERSONALIZED_DIR=str(folder/'personalized'),FLARE_FL_CANDIDATE=str(folder/'candidate.pth'),FLARE_FL_METRICS_JSON=str(folder/'metrics.json'),FLARE_FL_METRICS_CSV=str(folder/'metrics.csv'),FLARE_FL_TRUST_STATE=str(folder/'trust.json'))
  processes=[]
  def launch(name,command):
   with (folder/(name+'.log')).open('w') as log:process=subprocess.Popen(command,cwd=copy,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   processes.append(process);return process
  def stop():
   for child in reversed(processes):
    if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
   for child in processes:
    try:child.wait(10)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
  try:
   if a.suite=='selftests':
    for module in ['fl.compression','fl.privacy','fl.trust','fl.drift','fl.selection','fl.async_fl','fl.metrics','fl.distillation','fl.personalization']:
     child=launch(module,[sys.executable,'-m',module]);started=time.monotonic()
     try:code=child.wait(90);save(dict(capability=module,status='Passed' if code==0 else 'Failed',exit_code=code,duration_s=time.monotonic()-started,evidence=str(folder/(module+'.log'))))
     except subprocess.TimeoutExpired:save(dict(capability=module,status='Failed',error='90s bounded self-test timeout'));os.killpg(child.pid,signal.SIGTERM)
    continue
   port=(18780 if a.suite=='legacy' else 18580)+index;api=18700+index;flower=18590+index;env.update(SDN_HOST='127.0.0.1',SDN_PORT=str(port),FLARE_SIMULATION_URL=f'http://127.0.0.1:{port}',FL_SERVER_ADDRESS=f'127.0.0.1:{flower}',API_BASE_URL=f'http://127.0.0.1:{api}')
   if a.suite=='federation':
    path=copy/'config/swarm_config.yaml';cfg=yaml.safe_load(path.read_text());cfg.update(default_count=10,formation='circle',topology='full_mesh',seed=a.seed);cfg['radio']['range_m']=1000.;path.write_text(yaml.safe_dump(cfg))
    path=copy/'config/fl_config.yaml';cfg=yaml.safe_load(path.read_text());cfg['training']['local_epochs']=1;cfg['federation'].update(min_clients=5,fraction_fit=1.0);cfg['client_manager']['simulation_jammed_clients']=['drone_3'] if case=='jammed' else []
    if a.advanced:
     for section in ['compression','client_selection','async_fl']:cfg[section]['enabled']=True
     cfg['differential_privacy']['enabled']=True;cfg['differential_privacy']['client_side']['enabled']=True
    path.write_text(yaml.safe_dump(cfg))
   launch('simulation',[sys.executable,'-m','uvicorn','sdn.mock_sdn:app','--port',str(port)])
   headers={'Authorization':'Bearer antijam-development-sdn-token'}
   def call(method,path,body=None):
    response=requests.request(method,f'http://127.0.0.1:{port}/simulation'+path,headers=headers,json=body,timeout=15);response.raise_for_status();return response.json()
   deadline=time.monotonic()+45
   while True:
    try:
     response=requests.get(f'http://127.0.0.1:{port}/health',timeout=1)
     if response.ok:break
    except requests.RequestException:pass
    if time.monotonic()>deadline:raise AssertionError('Simulator startup timeout')
    time.sleep(.2)
   if a.suite=='legacy':
    launch('api',[sys.executable,'-m','uvicorn','api.server:app','--port',str(api)])
    deadline=time.monotonic()+60
    while True:
     try:
      if requests.get(env['API_BASE_URL']+'/health',timeout=1).ok:break
     except requests.RequestException:pass
     if time.monotonic()>deadline:raise AssertionError('API startup timeout')
     time.sleep(.2)
    for name in ['test_ew_api','test_fl_api','test_adversarial','test_api_client']:
     child=launch(name,[sys.executable,'tests/'+name+'.py']);started=time.monotonic();code=child.wait(180);log=(folder/(name+'.log')).read_text();save(dict(capability=name,status='Passed' if code==0 and '[FAIL]' not in log and '✗ FAIL' not in log else 'Failed',exit_code=code,duration_s=time.monotonic()-started,evidence=str(folder/(name+'.log'))))
    continue
   s=call('GET','/state');session=s['session_id']
   if case=='poisoned':call('POST','/attacks',dict(attack_id='audit-poison',session_id=session,expected_revision=s['revision'],group='fl',profile='poisoned',targets=['drone_1'],paths=[],duration_s=300))
   s=call('GET','/state')
   if a.transport=='satellite':
    for n in s['nodes']:
     receipt=call('POST','/policies',dict(policy_id='audit-satellite-'+n['id'],session_id=session,revision=s['revision'],source=n['id'],destination='base',traffic_class='telemetry',family='satellite',hops=[n['id'],'base']))
     assert receipt['applied'],'Satellite policy not installed'
   call('PATCH','/config',dict(expected_revision=s['revision'],speed=4))
   launch('flower',[sys.executable,'fl/server.py','--rounds','3']);launch('clients',[sys.executable,'-m','fl.client_manager','--server-address',f'127.0.0.1:{flower}','--reconcile-interval','.2'])
   s=call('GET','/state');call('POST','/control',dict(expected_revision=s['revision'],action='play'))
   cursor=0;events=[];deadline=time.monotonic()+300;last=0;pause_verified=False
   while time.monotonic()<deadline:
    page=call('GET',f'/events?after={cursor}');events.extend(page['events']);cursor=page['cursor']
    agg=[e for e in events if e['kind']=='aggregation_result' and 'security_audits' in e['data']]
    if agg and len(agg)!=last:print(json.dumps(dict(case=case,aggregation_rounds=[e['data'].get('round') for e in agg])),flush=True);last=len(agg)
    if not pause_verified and any(e['kind']=='upload_started' for e in events):
     s=call('GET','/state');paused=call('POST','/control',dict(expected_revision=s['revision'],action='pause'));time.sleep(.4);again=call('GET','/state');assert paused['simulation_time']==again['simulation_time'];assert paused['statistics']['delivered']==again['statistics']['delivered'];call('POST','/control',dict(expected_revision=again['revision'],action='play'));pause_verified=True
    if agg and max(e['data'].get('round',0) for e in agg)>=3:break
    if processes[-2].poll() is not None:break
    time.sleep(.2)
   state=call('GET','/state');page=call('GET',f'/events?after={cursor}');events.extend(page['events']);(folder/'events.json').write_text(json.dumps(events));(folder/'state.json').write_text(json.dumps(state));aggregations=[e['data'] for e in events if e['kind']=='aggregation_result' and 'security_audits' in e['data']];uploads=[e['data'] for e in events if e['kind']=='upload_completed'];assert len(aggregations)>=3,f'Only {len(aggregations)} rounds in 300s';assert any(t.get('status')=='delivered' for t in uploads),'No delivered real updates';assert pause_verified,'Upload pause observation was not exercised'
   if case=='poisoned':assert any(e['kind']=='poisoning_armed' for e in events),'No poison dispatch';assert any(r.get('action') in ('REJECTED','DOWN_WEIGHTED') for e in aggregations for r in e.get('security_audits',[])),'No observed protective update action'
   metrics=json.loads((folder/'metrics.json').read_text());assert metrics['n_rounds_completed']>=3;assert list(folder.glob('training/*/candidate.pth')),'No trained checkpoint'
   if a.advanced:
    latest=metrics['latest'];assert latest.get('privacy_epsilon',0)>0,'Enabled DP did not report privacy accounting'
    assert latest.get('compression_ratio',0)>1,'Enabled compression did not run'
    save(dict(capability='live asynchronous buffering',status='Passed' if latest.get('total_aggregations',0)>0 else 'Failed',evidence=str(folder/'metrics.json'),actual=latest.get('total_aggregations'),expected='FedBuff aggregation when enabled',limitation='Current live server only advances the buffer round; no updates enter FedBuff'))
    save(dict(capability='live adaptive selection',status='Failed',evidence=str(folder/'pool.json'),expected='Configured selector chooses clients',actual='Mission configure_fit chooses rotating eligible identities independently of _selector.select'))
   save(dict(capability='real Flower '+case,status='Passed',rounds=len(aggregations),uploads=len(uploads),pause_verified=pause_verified,synthetic_windows_per_client=500,local_epochs=1,pool_size=5,seed=a.seed,transport=a.transport,evidence=str(folder/'events.json')))
  except Exception as exc:save(dict(capability=a.suite+' '+case,status='Failed',error=f'{type(exc).__name__}: {exc}',evidence=str(folder)))
  finally:stop()
 assert protected_files(root)==before,'Operator inputs changed during isolated service suites'

if __name__=='__main__':main()
