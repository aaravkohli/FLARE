"""Mission proxy authorization and failure semantics with real JWT dependencies."""
import copy
from fastapi.testclient import TestClient
import api.server as server
import api.mission as mission


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
