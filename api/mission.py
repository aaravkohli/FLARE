"""User-authenticated proxy to the authoritative simulation SDN service."""
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from schemas.mission import MissionConfig, MissionControl, MissionCommand, MissionAttack, ClearMissionAttacks, MissionProbe, ExperimentRequest, ExperimentControl


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
    @router.get('/topologies')
    async def topologies(): return await service_request('GET','/topologies')
    @router.get('/routes')
    async def route(source: str,destination: str,traffic_class: str='sensor'):
        return await service_request('GET','/routes',params=dict(source=source,destination=destination,traffic_class=traffic_class))
    @router.get('/packet-events')
    async def packet_events(after: int=0,limit: int=2000):
        return await service_request('GET','/packet-events',params=dict(after=after,limit=limit))
    @router.post('/probes',dependencies=writes)
    async def probe(request: MissionProbe): return await service_request('POST','/probes',request.model_dump())
    @router.post('/experiments',dependencies=writes)
    async def create_experiment(request: ExperimentRequest): return await service_request('POST','/experiments',request.model_dump())
    @router.get('/experiments/{identity}')
    async def experiment(identity: str): return await service_request('GET','/experiments/'+identity)
    @router.post('/experiments/{identity}/control',dependencies=writes)
    async def experiment_control(identity: str,request: ExperimentControl):
        return await service_request('POST','/experiments/'+identity+'/control',request.model_dump())
    @router.get('/experiments/{identity}/export')
    async def experiment_export(identity: str,format: str='json'):
        from fastapi.responses import Response
        if format not in ('json','csv'): raise HTTPException(422,'Unsupported export format')
        state=await service_request('GET','/experiments/'+identity)
        import json, csv, io
        if format=='json': content=json.dumps(state,indent=2)
        else:
            from simulation.swarm.experiments import CSV_FIELDS
            fields=CSV_FIELDS
            output=io.StringIO(); writer=csv.DictWriter(output,fieldnames=fields,extrasaction='ignore'); writer.writeheader(); writer.writerows(state.get('results',[])); content=output.getvalue()
        return Response(content,media_type='application/json' if format=='json' else 'text/csv',
            headers={'Content-Disposition':f'attachment; filename="flare-{identity}.{format}"'})
    @router.get('/experiments/{identity}/traces/{filename}')
    async def experiment_trace(identity: str,filename: str):
        from fastapi.responses import Response
        state=await service_request('GET','/experiments/'+identity)
        if filename not in state.get('trace_files',[]): raise HTTPException(404,'Trace is unavailable')
        async with httpx.AsyncClient(timeout=30) as client:
            response=await client.get(service_url()+'/simulation/experiments/'+identity+'/traces/'+filename,
                headers={'Authorization':'Bearer '+os.getenv('AJ_SDN_TOKEN','antijam-development-sdn-token')})
        if not response.is_success: raise HTTPException(response.status_code,'Trace retrieval failed')
        return Response(response.content,media_type='application/gzip',headers={'Content-Disposition':f'attachment; filename="{filename}"'})
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
    cursors={}; packet_cursors={}
    while True:
        if clients and os.getenv('FLARE_SWARM_ENABLED')=='1':
            try:
                snapshot=await service_request('GET','/state',params={'after':min(cursors.values(),default=0)})
                packet_session=snapshot['session_id']
                for ws in clients:
                    if ws not in packet_cursors and getattr(ws.state,'packet_session_id',None)==packet_session and getattr(ws.state,'packet_cursor',None) is not None:
                        packet_cursors[ws]=(packet_session,min(ws.state.packet_cursor,snapshot.get('packet_cursor',0)))
                packet_after=min((value[1] for value in packet_cursors.values() if value[0]==packet_session),default=max(0,snapshot.get('packet_cursor',0)-4000))
                packets=await service_request('GET','/packet-events',params={'after':packet_after,'limit':5000})
                for ws in list(clients):
                    cursor=cursors.get(ws,0)
                    frame=dict(snapshot,events=[e for e in snapshot['events'] if e['cursor']>cursor])
                    try:
                        cursors[ws]=snapshot['cursor']
                        previous=packet_cursors.get(ws)
                        after=previous[1] if previous and previous[0]==packet_session else packet_after
                        batch=dict(packets,events=[e for e in packets['events'] if e['cursor']>after],event_gap=after>0 and after<packets.get('oldest_cursor',1)-1)
                        async with asyncio.timeout(1):
                            await ws.send_json({'type':'mission_snapshot','data':frame})
                            await ws.send_json({'type':'mission_packet_events','data':batch})
                        packet_cursors[ws]=(packet_session,max(after,packets['cursor']))
                    except Exception:
                        cursors.pop(ws,None); packet_cursors.pop(ws,None)
                        if ws in clients: clients.remove(ws)
                        # A timed-out subscriber must reconnect with its cursor;
                        # retaining it would repeatedly replay oversized batches.
                        try:
                            async with asyncio.timeout(1): await ws.close(code=1013)
                        except Exception: pass
                for ws in set(cursors)-set(clients): cursors.pop(ws,None); packet_cursors.pop(ws,None)
            except HTTPException: pass
        await asyncio.sleep(.2)
