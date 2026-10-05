"""User-authenticated proxy to the authoritative simulation SDN service."""
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from schemas.mission import MissionConfig, MissionControl, MissionCommand, MissionAttack, ClearMissionAttacks


def service_url():
    return os.getenv('FLARE_SIMULATION_URL',f"http://{os.getenv('SDN_HOST','127.0.0.1')}:{os.getenv('SDN_PORT','8080')}")

async def service_request(method, path, body=None, params=None):
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response=await client.request(method,service_url()+'/simulation'+path,json=body,params=params,
                headers={'Authorization':'Bearer '+os.getenv('AJ_SDN_TOKEN','antijam-development-sdn-token')})
        except httpx.HTTPError as exc:
            raise HTTPException(503,'Authoritative simulation service is unavailable') from exc
    if response.status_code>=400:
        try: detail=response.json().get('detail','Simulation request failed')
        except ValueError: detail='Simulation request failed'
        raise HTTPException(response.status_code,detail)
    return response.json()

def mission_router(current_user, admin, runtime_mode):
    router=APIRouter(prefix='/mission/simulation',tags=['mission simulation'],dependencies=[Depends(current_user)])
    def simulation_only():
        if runtime_mode()[0]!='simulation': raise HTTPException(409,'Mission mutations require simulation mode')
    writes=[Depends(admin),Depends(simulation_only)]
    @router.get('')
    async def snapshot(after: int=0): return await service_request('GET','/state',params={'after':after})
    @router.patch('/config',dependencies=writes)
    async def config(request: MissionConfig): return await service_request('PATCH','/config',request.model_dump(exclude_none=True))
    @router.post('/control',dependencies=writes)
    async def control(request: MissionControl): return await service_request('POST','/control',request.model_dump())
    @router.post('/commands',dependencies=writes)
    async def command(request: MissionCommand): return await service_request('POST','/commands',request.model_dump())
    @router.get('/commands/{identity}')
    async def command_status(identity: str): return await service_request('GET','/commands/'+identity)
    @router.get('/scenarios')
    async def scenarios(): return await service_request('GET','/scenarios')
    @router.get('/attacks')
    async def attacks(): return await service_request('GET','/attacks')
    @router.post('/attacks',dependencies=writes)
    async def attack(request: MissionAttack): return await service_request('POST','/attacks',request.model_dump())
    @router.post('/attacks/clear',dependencies=writes)
    async def clear(request: ClearMissionAttacks): return await service_request('POST','/attacks/clear',request.model_dump())
    @router.get('/runtime')
    async def runtime():
        from api.server import _fl_client_manager_status, _deployment_watcher
        state=await service_request('GET','/runtime')
        state['clients']=_fl_client_manager_status(); state['models']=_deployment_watcher.status()
        return state
    @router.post('/scenarios/{name}',dependencies=writes)
    async def scenario(name: str,request: MissionControl): return await service_request('POST','/scenarios/'+name,request.model_dump())
    @router.get('/events')
    async def events(after: int=0,category: str|None=None,node: str|None=None):
        return await service_request('GET','/events',params={k:v for k,v in {'after':after,'category':category,'node':node}.items() if v is not None})
    return router

async def mission_broadcaster(clients):
    import asyncio
    cursors={}
    while True:
        if clients and os.getenv('FLARE_SWARM_ENABLED')=='1':
            try:
                snapshot=await service_request('GET','/state',params={'after':min(cursors.values(),default=0)})
                for ws in list(clients):
                    cursor=cursors.get(ws,0)
                    frame=dict(snapshot,events=[e for e in snapshot['events'] if e['cursor']>cursor])
                    try:
                        async with asyncio.timeout(1): await ws.send_json({'type':'mission_snapshot','data':frame})
                        cursors[ws]=snapshot['cursor']
                    except Exception: cursors.pop(ws,None)
                for ws in set(cursors)-set(clients): cursors.pop(ws,None)
            except HTTPException: pass
        await asyncio.sleep(.2)
