"""Authenticated real-service audit; refuses the operator's default ports."""
import argparse
import json
from pathlib import Path
import time
import requests


def main():
 p=argparse.ArgumentParser();p.add_argument('--api',default='http://127.0.0.1:18200');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.api.rstrip('/').endswith((':8000',':8001')):raise RuntimeError('Use an audit-owned API port')
 a.output.mkdir(parents=True,exist_ok=True);rows=[];session=requests.Session()
 def check(name,fn):
  row={'capability':name,'status':'Not Tested'}
  try:row.update(evidence=fn(),status='Passed')
  except Exception as e:row.update(status='Failed',error=f'{type(e).__name__}: {e}')
  rows.append(row);(a.output/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
 def req(method,path,data=None,expected=200):
  r=session.request(method,a.api+path,json=data,timeout=45);assert r.status_code==expected,(path,r.status_code,r.text[:500]);return r.json() if 'json' in r.headers.get('content-type','') else r.text
 login=requests.post(a.api+'/auth/token',data={'username':'admin','password':'antijam2026'},timeout=20);login.raise_for_status();session.headers['Authorization']='Bearer '+login.json()['access_token']
 openapi=req('GET','/openapi.json');(a.output/'openapi.json').write_text(json.dumps(openapi,indent=2))
 def auth_all():
  protected=[(path,method) for path,ops in openapi['paths'].items() for method,op in ops.items() if method in ('get','post','patch','put','delete') and op.get('security')]
  outcomes=[]
  for path,method in protected:
   url=path.replace('{drone_id}','drone_1').replace('{identity}','0'*32).replace('{filename}','missing.jsonl.gz')
   r=requests.request(method,a.api+url,json={} if method!='get' else None,timeout=10)
   assert r.status_code in (401,403),(method,path,r.status_code)
   outcomes.append({'method':method,'path':path,'response':r.status_code})
  return outcomes
 check('every protected OpenAPI operation rejects unauthenticated calls',auth_all)
 for path in ['/health','/ready','/api/health','/api/status','/ready/history','/fleet/drones','/fleet/drones/manage','/fleet/clients','/swarm/status','/swarm/metrics','/metrics/live','/metrics/history','/mission/events','/swarm/insider','/api/fl/config','/api/fl/metrics','/mission/simulation/topologies','/mission/simulation/scenarios','/mission/simulation/runtime','/mission/simulation/events','/mission/simulation/attacks']:
  check('GET '+path,lambda path=path:{'response':req('GET',path)})
 mission='/mission/simulation'
 def state():return req('GET',mission)
 def mutate(path,body,method='POST'):
  s=state();payload=dict(body,expected_revision=s['revision']);
  if path not in ('/control','/config'):payload['session_id']=s['session_id']
  return req(method,mission+path,payload)
 check('reset creates a fresh paused mission',lambda:mutate('/control',{'action':'reset'}))
 ids=[n['id'] for n in state()['nodes']]
 def telemetry():
  mutate('/control',{'action':'play'});deadline=time.monotonic()+30
  while time.monotonic()<deadline:
   value=req('GET','/metrics/live?drone_id='+ids[0])
   if value and isinstance(value,dict) and value.get('paths'):return value
   time.sleep(.5)
  raise AssertionError('No real orchestrator telemetry')
 check('live telemetry originates from the playing authoritative mission',telemetry)
 def route_probe():
  s=state();path='/routes?source='+ids[0]+'&destination=base';route=req('GET',mission+path)
  assert route['source']==ids[0]
  probe=mutate('/probes',{'source':ids[0],'destination':'base','size_bytes':512})
  packet=probe['packet'];assert packet['source']==ids[0]
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   events=req('GET',mission+'/packet-events?after=0')['events']
   matches=[e for e in events if e['data']['id']==packet['id'] and e['kind']=='packet_result']
   if matches:return {'route':route,'packet_result':matches[-1]}
   time.sleep(.2)
  raise AssertionError('Probe never obtained a terminal packet event')
 check('route inspection / explicit probe / packet-event terminal outcome',route_probe)
 def commands():
  identity='audit-hold';record=mutate('/commands',{'command_id':identity,'targets':[ids[0]],'action':'hold','parameters':{},'ttl_s':10})
  deadline=time.monotonic()+20
  while time.monotonic()<deadline:
   record=req('GET',mission+'/commands/'+identity)
   if all(v['status'] not in ('pending','queued','executed_ack_pending') for v in record['outcomes'].values()):return record
   time.sleep(.3)
  raise AssertionError('Command did not reach a terminal outcome')
 check('command submission and read-back terminal accounting',commands)
 def frames():
  import asyncio,websockets
  async def read():
   async with websockets.connect(a.api.replace('http','ws',1)+'/ws') as socket:
    await socket.send(json.dumps({'type':'auth','token':session.headers['Authorization'].split()[1]}))
    authenticated=json.loads(await socket.recv());assert authenticated=={'type':'auth','status':'ok'}
    messages=[]
    for _ in range(15):
     value=json.loads(await asyncio.wait_for(socket.recv(),15));messages.append(value.get('type','payload'))
     if value.get('type')=='mission_snapshot':break
    assert 'mission_snapshot' in messages;return {'received_types':messages}
  return asyncio.run(read())
 check('authenticated WebSocket delivers actual service messages',frames)
 def sse():
  with session.get(a.api+'/stream',stream=True,timeout=(5,10)) as r:
   assert r.status_code==200 and 'text/event-stream' in r.headers['content-type']
   for line in r.iter_lines(chunk_size=1):
    if line:return {'first_frame':line.decode()[:500]}
   raise AssertionError('SSE returned no frame')
 check('authenticated SSE produces stream frames',sse)
 check('report export returns real downloadable HTML',lambda:{'html_prefix':str(req('GET','/api/report/generate'))[:200]})
 for path in ['/swarm/insider/'+ids[0],'/explanations/routing/'+ids[0]]:check('GET '+path,lambda path=path:req('GET',path))
 def pause():
  before=mutate('/control',{'action':'pause'});time.sleep(.6);after=state()
  assert before['session_id']==after['session_id'] and before['simulation_time']==after['simulation_time']
  assert before['statistics']['delivered']==after['statistics']['delivered']
  return {'simulation_time':after['simulation_time'],'delivered':after['statistics']['delivered'],'wall_observation_s':.6}
 check('pause freezes simulation clock and packet delivery',pause)
 print('Live API checks finished',flush=True)
if __name__=='__main__':main()
