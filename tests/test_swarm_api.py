"""Mission proxy authorization and failure semantics with real JWT dependencies."""
import copy
from fastapi.testclient import TestClient
import api.server as server
import api.mission as mission


def test_adaptive_study_is_additive_authenticated_and_validated(monkeypatch):
    monkeypatch.setenv('MODE','simulation');calls=[]
    async def upstream(method,path,body=None,params=None):
        calls.append((method,path,body));return {'id':'study','mode':'adaptive_study','status':'starting','results':[]}
    monkeypatch.setattr(mission,'service_request',upstream)
    client=TestClient(server.app)
    admin={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    payload={'mode':'adaptive_study','protocol_id':'adaptive_resilience_v1','preset':'pilot'}
    assert client.post('/mission/simulation/experiments',json=payload).status_code==401
    assert client.post('/mission/simulation/experiments',headers=admin,json=payload).status_code==200
    assert calls[-1]==('POST','/experiments',payload)
    assert client.post('/mission/simulation/experiments',headers=admin,json=payload|{'preset':'other'}).status_code==422
    assert client.post('/mission/simulation/experiments',headers=admin,json=payload|{'seeds':[7]}).status_code==422
    assert len(calls)==1


def test_topology_mode_is_additive_authenticated_and_forwarded(monkeypatch):
    monkeypatch.setenv('MODE','simulation');calls=[]
    async def upstream(method,path,body=None,params=None):
        calls.append((method,path,body));return {'topology':'ring','topology_selection':{'mode':'auto'}}
    monkeypatch.setattr(mission,'service_request',upstream)
    client=TestClient(server.app)
    admin={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    payload={'expected_revision':3,'topology_mode':'auto'}
    assert client.patch('/mission/simulation/config',json=payload).status_code==401
    assert client.patch('/mission/simulation/config',headers=admin,json=payload).status_code==200
    assert calls[-1]==('PATCH','/config',payload)
    assert client.patch('/mission/simulation/config',headers=admin,json=payload|{'topology_mode':'bogus'}).status_code==422
    legacy={'expected_revision':3,'topology':'chain'}
    assert client.patch('/mission/simulation/config',headers=admin,json=legacy).status_code==200
    assert calls[-1][2]==legacy


def test_mission_reads_allow_users_but_mutations_require_admin_and_simulation(monkeypatch):
    users=copy.deepcopy(server._USERS)
    users['swarm-viewer']={'username':'swarm-viewer','role':'viewer'}
    monkeypatch.setattr(server,'_USERS',users)
    calls=[]
    async def upstream(method,path,body=None,params=None):
        calls.append((method,path));return {'session_id':'test','revision':1}
    monkeypatch.setattr(mission,'service_request',upstream)
    client=TestClient(server.app)
    viewer={'Authorization':'Bearer '+server._create_access_token({'sub':'swarm-viewer'})}
    admin={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    assert client.get('/mission/simulation').status_code==401
    assert client.get('/mission/simulation',headers=viewer).status_code==200
    payload={'action':'play','expected_revision':1}
    assert client.post('/mission/simulation/control',headers=viewer,json=payload).status_code==403
    monkeypatch.setenv('MODE','real')
    assert client.post('/mission/simulation/control',headers=admin,json=payload).status_code==409
    monkeypatch.setenv('MODE','simulation')
    assert client.post('/mission/simulation/control',headers=admin,json=payload).status_code==200
    assert calls==[('GET','/state'),('POST','/control')]


def test_unavailable_authoritative_service_returns_503_without_fake_snapshot(monkeypatch):
    import asyncio
    import httpx
    from fastapi import HTTPException
    class OfflineClient:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def request(self,*args,**kwargs):raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(mission.httpx,'AsyncClient',lambda **_:OfflineClient())
    try:asyncio.run(mission.service_request('GET','/state'))
    except HTTPException as exc:assert exc.status_code==503
    else:raise AssertionError('Unavailable mission service must fail closed')


def test_attack_admin_boundary_capabilities_and_proxy_payload(monkeypatch):
    users=copy.deepcopy(server._USERS);users['attack-viewer']={'username':'attack-viewer','role':'viewer'}
    monkeypatch.setattr(server,'_USERS',users);monkeypatch.setenv('MODE','simulation')
    calls=[]
    async def upstream(method,path,body=None,params=None):calls.append((method,path,body));return {'accepted':True}
    monkeypatch.setattr(mission,'service_request',upstream);client=TestClient(server.app)
    viewer={'Authorization':'Bearer '+server._create_access_token({'sub':'attack-viewer'})};admin={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    payload={'session_id':'current','expected_revision':2,'attack_id':'one','profile':'dos','targets':['drone_1'],'paths':['satellite'],'duration_s':15}
    assert client.get('/mission/simulation/attacks').status_code==401
    assert client.get('/mission/simulation/attacks',headers=viewer).status_code==200
    assert client.post('/mission/simulation/attacks',headers=viewer,json=payload).status_code==403
    assert client.post('/mission/simulation/attacks',headers=admin,json=payload).status_code==200
    assert calls[-1][2]['targets']==['drone_1'] and calls[-1][2]['session_id']=='current'
    assert client.post('/mission/simulation/attacks/clear',headers=admin,json={'session_id':'current','expected_revision':3}).status_code==200


def test_fl_reads_do_not_promote_results_from_another_session(monkeypatch):
    import asyncio
    monkeypatch.setenv('FLARE_SWARM_ENABLED','1')
    async def upstream(*args,**kwargs):return {'session_id':'new','playing':False}
    monkeypatch.setattr(mission,'service_request',upstream)
    stale={'latest':{'round':3,'mission_session':'old','threat_f1':.9},'history':[{'mission_session':'old'}]}
    result=asyncio.run(server._current_mission_fl_metrics(stale))
    assert not result['available'] and result['history']==[] and result['status']=='waiting_for_play'
    monkeypatch.setenv('FLARE_FL_METRICS_JSON','/private/tmp/flare-no-metrics-for-session-test.json')
    absent=asyncio.run(server.get_fl_metrics({}));assert absent['available'] is False and absent['latest'].get('threat_f1') is None
    current={'latest':{'round':4,'mission_session':'new'},'history':[{'mission_session':'old'},{'mission_session':'new'}]}
    result=asyncio.run(server._current_mission_fl_metrics(current));assert result['available'] and len(result['history'])==1


def test_mesh_inspection_and_experiments_keep_authorization_boundaries(monkeypatch):
    users=copy.deepcopy(server._USERS)
    users['mesh-viewer']={'username':'mesh-viewer','role':'viewer'}
    monkeypatch.setattr(server,'_USERS',users);monkeypatch.setenv('MODE','simulation')
    calls=[]
    async def upstream(method,path,body=None,params=None):
        calls.append((method,path,body,params));return {'id':'a'*32,'status':'paused','results':[]}
    monkeypatch.setattr(mission,'service_request',upstream)
    client=TestClient(server.app)
    viewer={'Authorization':'Bearer '+server._create_access_token({'sub':'mesh-viewer'})}
    admin={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    for route in ['/topologies','/routes?source=a&destination=b','/packet-events?after=1','/experiments/'+'a'*32]:
        assert client.get('/mission/simulation'+route).status_code==401
        assert client.get('/mission/simulation'+route,headers=viewer).status_code==200
    probe=dict(session_id='s',expected_revision=1,source='a',destination='b')
    assert client.post('/mission/simulation/probes',headers=viewer,json=probe).status_code==403
    assert client.post('/mission/simulation/probes',headers=admin,json=probe).status_code==200
    request=dict(mode='compare',topologies=['star','ring'])
    assert client.post('/mission/simulation/experiments',headers=viewer,json=request).status_code==403
    assert client.post('/mission/simulation/experiments',headers=admin,json=request).status_code==200
    assert client.post('/mission/simulation/experiments/'+'a'*32+'/control',headers=viewer,json={'action':'play'}).status_code==403
    monkeypatch.setenv('MODE','real')
    assert client.post('/mission/simulation/experiments',headers=admin,json=request).status_code==409


def test_packet_websocket_resume_cursor_validation():
    from starlette.websockets import WebSocketDisconnect
    import pytest
    client=TestClient(server.app)
    token=server._create_access_token({'sub':server.ADMIN_USERNAME})
    with client.websocket_connect('/ws') as ws:
        ws.send_json(dict(type='auth',token=token,packet_session_id='session',packet_cursor=42))
        assert ws.receive_json()=={'type':'auth','status':'ok'}
    for cursor in (-1,True,'42'):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws') as ws:
                ws.send_json(dict(type='auth',token=token,packet_session_id='session',packet_cursor=cursor));ws.receive_json()


def test_failed_mission_subscriber_is_closed_without_starving_healthy_subscribers(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    monkeypatch.setenv('FLARE_SWARM_ENABLED','1')
    async def run():
        ready=asyncio.Event()
        class Subscriber:
            def __init__(self,broken=False):self.state=SimpleNamespace();self.broken=broken;self.frames=[];self.closed=None
            async def send_json(self,frame):
                if self.broken:raise RuntimeError('fault-injected disconnected subscriber')
                self.frames.append(frame)
                if len(self.frames)==2:ready.set()
            async def close(self,code):self.closed=code
        broken=Subscriber(True);healthy=Subscriber();subscribers=[broken,healthy]
        async def upstream(method,path,body=None,params=None):
            if path=='/state':return {'session_id':'s','events':[],'cursor':0,'packet_cursor':0}
            return {'session_id':'s','events':[],'cursor':0,'latest_cursor':0,'oldest_cursor':1}
        monkeypatch.setattr(mission,'service_request',upstream)
        task=asyncio.create_task(mission.mission_broadcaster(subscribers))
        try:await asyncio.wait_for(ready.wait(),2)
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        assert subscribers==[healthy] and broken.closed==1013
        assert [frame['type'] for frame in healthy.frames]==['mission_snapshot','mission_packet_events']
    asyncio.run(run())


def test_prior_session_route_and_containment_updates_cannot_cross_reset(monkeypatch):
    from fleet.registry import list_drones
    from simulation.swarm.engine import SwarmEngine
    from sdn.mock_sdn import app as controller,SDN_API_TOKEN
    engine=SwarmEngine(list_drones(),isolated=True);old=engine.session_id;engine.reset();identity=next(iter(engine.nodes))
    monkeypatch.setattr(controller.state,'swarm',engine)
    client=TestClient(controller);headers={'Authorization':'Bearer '+SDN_API_TOKEN,'X-FLARE-Mission-Session':old}
    assert client.post('/sdn/route',headers=headers,json={'drone_id':identity,'path_name':'hold','action_id':3}).status_code==409
    assert client.post('/sdn/containment',headers=headers,json={'drone_id':identity,'mode':'quarantined'}).status_code==409
    assert identity not in engine.security and engine.nodes[identity].containment=='normal'
    assert engine.nodes[identity].selected_route!='hold'
    headers['X-FLARE-Mission-Session']=engine.session_id
    assert client.post('/sdn/route',headers=headers,json={'drone_id':identity,'path_name':'hold','action_id':3}).status_code==200
    assert engine.nodes[identity].selected_route=='hold'
    # Service-token clients using the retained legacy contract still work.
    headers.pop('X-FLARE-Mission-Session')
    assert client.post('/sdn/containment',headers=headers,json={'drone_id':identity,'mode':'normal'}).status_code==200


def test_orchestrator_sends_session_binding_and_never_retries_stale_decisions():
    import asyncio,httpx
    from orchestrator.loop import _push_to_sdn,_push_containment
    async def run():
        calls=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(409,json={'detail':'Stale mission session'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            import pytest
            with pytest.raises(httpx.HTTPStatusError):await _push_to_sdn(client,'hold','drone_1',{'session_id':'old'})
            with pytest.raises(httpx.HTTPStatusError):await _push_containment(client,'drone_1','normal','test',mission_session='old')
        assert len(calls)==2
        assert all(r.headers['X-FLARE-Mission-Session']=='old' for r in calls)
    asyncio.run(run())


def test_session_change_clears_orchestrator_acknowledgments_rewards_and_containment_cache(monkeypatch):
    import asyncio,httpx
    from collections import deque
    from orchestrator.loop import Orchestrator
    actor=Orchestrator.__new__(Orchestrator);actor._fl_model=None;actor._mission_session='old';actor._metric_history={'a':deque([1])};actor._rl_agents={}
    for name,value in [('_prev_path','satellite'),('_prev_path_time',12.),('_last_good_decision',{'path_name':'satellite'}),('_last_good_ts',12.),('_prev_rewards',3.),('_containment_modes','quarantined')]:setattr(actor,name,{'a':value})
    monkeypatch.setenv('FLARE_SWARM_ENABLED','1')
    async def upstream(method,path,body=None,params=None):return {'session_id':'new','playing':False,'simulation_time':0.,'metrics':{}}
    monkeypatch.setattr(mission,'service_request',upstream)
    async def run():
        async with httpx.AsyncClient() as client:assert await actor._collect_metrics(client)=={}
    asyncio.run(run())
    assert actor._mission_session=='new' and not actor._metric_history['a']
    assert actor._prev_path['a'] is None and actor._last_good_decision['a'] is None
    assert actor._prev_rewards['a']==0. and actor._last_good_ts['a']==0.
    assert actor._containment_modes['a']=='normal'


def test_safeguarded_policy_and_pilot_preserve_auth_and_validation(monkeypatch):
    monkeypatch.setenv('MODE','simulation');calls=[]
    async def upstream(method,path,body=None,params=None):calls.append((path,body));return {'accepted':True}
    monkeypatch.setattr(mission,'service_request',upstream);client=TestClient(server.app)
    headers={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    cfg={'expected_revision':3,'topology_mode':'auto','adaptive_policy':'evidence_resilience_v1'}
    assert client.patch('/mission/simulation/config',json=cfg).status_code==401
    assert client.patch('/mission/simulation/config',headers=headers,json=cfg).status_code==200
    assert calls[-1][1]==cfg
    assert client.patch('/mission/simulation/config',headers=headers,json=cfg|{'adaptive_policy':'fake'}).status_code==422
    pilot={'mode':'adaptive_study','protocol_id':'evidence_resilience_pilot_v1','preset':'pilot'}
    assert client.post('/mission/simulation/experiments',headers=headers,json=pilot).status_code==200
    assert client.post('/mission/simulation/experiments',headers=headers,json=pilot|{'preset':'evidence'}).status_code==422
    assert calls[-1][1]==pilot


def test_autonomy_config_auth_profile_validation_and_forwarding(monkeypatch):
    monkeypatch.setenv('MODE','simulation');calls=[]
    async def upstream(method,path,body=None,params=None):calls.append((path,body));return {'accepted':True}
    monkeypatch.setattr(mission,'service_request',upstream);client=TestClient(server.app)
    headers={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})}
    payload={'expected_revision':4,'autonomy_mode':'recovery_v1','safety_profile':{'geofence_m':2500,'navigation_confidence':None}}
    assert client.patch('/mission/simulation/config',json=payload).status_code==401
    assert client.patch('/mission/simulation/config',headers=headers,json=payload).status_code==200
    assert calls[-1][1]['safety_profile']['navigation_confidence'] is None
    assert calls[-1][1]['autonomy_mode']=='recovery_v1'
    assert client.patch('/mission/simulation/config',headers=headers,json=payload|{'autonomy_mode':'unsafe'}).status_code==422
    assert client.patch('/mission/simulation/config',headers=headers,json=payload|{'safety_profile':{'critical_battery':.5}}).status_code==422


def test_affected_assistance_config_keeps_existing_authorization_and_proxy_contract(monkeypatch):
    monkeypatch.setenv('MODE','simulation');calls=[]
    async def upstream(method,path,body=None,params=None):
        calls.append((method,path,body));return {'topology_selection':{'assistance':{'enabled':True}}}
    monkeypatch.setattr(mission,'service_request',upstream)
    users=copy.deepcopy(server._USERS);users['assist-viewer']={'username':'assist-viewer','role':'viewer'};monkeypatch.setattr(server,'_USERS',users)
    client=TestClient(server.app);admin={'Authorization':'Bearer '+server._create_access_token({'sub':server.ADMIN_USERNAME})};viewer={'Authorization':'Bearer '+server._create_access_token({'sub':'assist-viewer'})}
    body={'expected_revision':9,'affected_uav_assist':True}
    assert client.patch('/mission/simulation/config',json=body).status_code==401
    assert client.patch('/mission/simulation/config',headers=viewer,json=body).status_code==403
    assert client.patch('/mission/simulation/config',headers=admin,json=body).status_code==200
    assert calls==[('PATCH','/config',body)]
    assert client.patch('/mission/simulation/config',headers=admin,json=body|{'affected_uav_assist':'invalid'}).status_code==422
