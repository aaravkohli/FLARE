"""Authenticated simulator service; fixed-step time is independent of browsers."""
from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import time
from fastapi import APIRouter, Depends, HTTPException
from schemas.mission import MissionConfig, MissionControl, MissionCommand, FLTransfer, RoutePolicy, MissionAttack, ClearMissionAttacks, MissionProbe, ExperimentJobRequest, ExperimentControl
from simulation.swarm.engine import SwarmEngine, RevisionConflict, SCENARIOS
from sdn.enforcement import SimulationSDNAdapter
from fleet.registry import list_drones
from simulation.swarm.topology import topology_metadata
from simulation.swarm.experiments import ExperimentManager
from fastapi.responses import Response, FileResponse


def attach_simulation(app, authenticate):
    app.state.swarm = None
    app.state.experiments = None
    @asynccontextmanager
    async def lifespan(application):
        task = None
        if os.getenv('FLARE_SWARM_ENABLED') == '1':
            registry = Path(os.environ['FLARE_FLEET_REGISTRY'])
            if registry.resolve() == Path('config/fleet_registry.yaml').resolve():
                raise RuntimeError('swarm requires a separate mission registry')
            application.state.swarm = SwarmEngine(list_drones(), registry_path=registry)
            application.state.experiments = ExperimentManager(os.getenv('FLARE_EXPERIMENT_DIR',str(registry.parent/'experiments')))
            async def clock():
                previous = time.monotonic()
                while True:
                    await asyncio.sleep(.025)
                    now = time.monotonic()
                    application.state.swarm.tick(now-previous)
                    previous = now
            task = asyncio.create_task(clock())
        try:
            yield
        finally:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
            if application.state.experiments: application.state.experiments.close()
            application.state.swarm = None
            application.state.experiments = None
    app.router.lifespan_context = lifespan
    router = APIRouter(prefix='/simulation', dependencies=[Depends(authenticate)])
    def engine():
        if app.state.swarm is None: raise HTTPException(409, 'Large-swarm simulation is not enabled')
        return app.state.swarm
    def mutate(call):
        try: return call()
        except RevisionConflict as exc: raise HTTPException(409, str(exc)) from exc
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    @router.get('/session')
    async def session():
        e=engine()
        return {'session_id':e.session_id,'seed':e.seed,'revision':e.revision,'simulation_time':e.seconds,'playing':e.playing,
            'eligible_clients':[n.id for n in e.nodes.values() if n.available and n.health>0 and n.containment=='normal'],
            'adaptive_policy':e.cfg.get('adaptive_policy','balanced_v1'),'observation_epoch':e.observability.epoch}
    @router.get('/state')
    async def state(after: int = 0): return engine().snapshot(after)
    @router.get('/topologies')
    async def topologies(): return topology_metadata()
    @router.get('/routes')
    async def route(source: str,destination: str,traffic_class: str='sensor'):
        return mutate(lambda:engine().inspect_route(source,destination,traffic_class))
    @router.post('/probes')
    async def probe(request: MissionProbe): return mutate(lambda:engine().probe(request))
    @router.get('/packet-events')
    async def packet_events(after: int=0,limit: int=2000):
        if after<0: raise HTTPException(422,'cursor must be nonnegative')
        return engine().packet_trace(after,limit)
    def experiments():
        engine()
        return app.state.experiments
    @router.post('/experiments')
    async def create_experiment(request: ExperimentJobRequest):
        return mutate(lambda:experiments().create(request,engine()))
    @router.get('/experiments/{identity}')
    async def experiment(identity: str): return mutate(lambda:experiments().state(identity))
    @router.post('/experiments/{identity}/control')
    async def experiment_control(identity: str,request: ExperimentControl):
        return mutate(lambda:experiments().control(identity,request.action))
    @router.get('/experiments/{identity}/export')
    async def experiment_export(identity: str,format: str='json'):
        content=mutate(lambda:experiments().export(identity,format))
        return Response(content,media_type={'json':'application/json','csv':'text/csv','md':'text/markdown','manifest':'application/json'}.get(format,'text/plain'),
            headers={'Content-Disposition':f'attachment; filename="flare-{identity}.{format}"'})
    @router.get('/experiments/{identity}/traces/{filename}')
    async def experiment_trace(identity: str,filename: str):
        state=mutate(lambda:experiments().state(identity))
        if filename not in state.get('trace_files',[]): raise HTTPException(404,'Trace is unavailable')
        return FileResponse(experiments().directory/identity/filename,media_type='application/gzip',filename=filename)
    @router.get('/metrics')
    async def metrics():
        e=engine()
        return {'session_id':e.session_id,'simulation_time':e.seconds,'playing':e.playing,'metrics':e.metrics(),
                'observability':e.observability.snapshot()}
    @router.get('/attacks')
    async def attacks(): return engine().attacks.state()
    @router.post('/attacks')
    async def attack(request: MissionAttack): return mutate(lambda:engine().inject_attack(request))
    @router.post('/attacks/clear')
    async def clear_attacks(request: ClearMissionAttacks): return mutate(lambda:engine().clear_attacks(request))
    @router.get('/runtime')
    async def runtime(): return engine().runtime_status()
    @router.post('/runtime')
    async def heartbeat(payload: dict):
        e=engine()
        if payload.get('session_id')!=e.session_id: raise HTTPException(409,'Stale runtime heartbeat')
        component=payload.get('component')
        if component not in ('orchestrator','flower'): raise HTTPException(422,'Unsupported runtime component')
        e.runtime[component]=dict(payload,timestamp=time.time()); return {'accepted':True}
    @router.patch('/config')
    async def configure(request: MissionConfig): return mutate(lambda: engine().configure(request))
    @router.post('/control')
    async def control(request: MissionControl): return mutate(lambda: engine().control(request))
    @router.post('/commands')
    async def command(request: MissionCommand):
        if request.session_id is None or request.expected_revision is None: raise HTTPException(422,'Command requires session_id and expected_revision')
        return mutate(lambda: engine().command(request))
    @router.get('/commands/{identity}')
    async def command_status(identity: str):
        record=engine().commands.get(identity)
        if record is None: raise HTTPException(404,'Unknown command')
        return record
    @router.get('/scenarios')
    async def scenarios(): return SCENARIOS
    @router.post('/scenarios/{name}')
    async def scenario(name: str, request: MissionControl):
        return mutate(lambda: engine().load_scenario(name,request.expected_revision))
    @router.get('/events')
    async def events(after: int=0, category: str|None=None, node: str|None=None):
        e=engine()
        rows=[r for r in e.events if r['cursor']>after and (category is None or r['category']==category)
              and (node is None or node in str(r['data']))]
        page=rows[:200]
        return {'session_id':e.session_id,'cursor':page[-1]['cursor'] if page else e.event_cursor,'latest_cursor':e.event_cursor,
            'event_gap':bool(e.events and after>0 and after<e.events[0]['cursor']-1),'events':page}
    @router.get('/capabilities')
    async def capabilities(): return SimulationSDNAdapter.capabilities
    @router.post('/policies')
    async def policy(request: RoutePolicy): return mutate(lambda: engine().install_policy(request))
    @router.post('/fl/transfers')
    async def upload(request: FLTransfer): return mutate(lambda: engine().submit_transfer(request))
    @router.get('/fl/transfers/{identity}')
    async def receipt(identity: str):
        record=engine().transfers.get(identity)
        if record is None: raise HTTPException(404,'Transfer does not belong to current session')
        return dict(record,evidence='packet_simulation')
    @router.post('/fl/events')
    async def fl_event(event: dict):
        e=engine()
        if event.get('session_id')!=e.session_id: raise HTTPException(409,'Stale FL session')
        if event.get('kind')=='round_dispatched':
            mutate(lambda:e.fl_dispatch(event.get('round'),event.get('participants')))
        if event.get('kind')=='aggregation_result':
            e.runtime['flower'].update(status='round_complete',round=event.get('round'),timestamp=time.time())
        e.event('FL',event.get('kind','aggregation_result'),event)
        for identity,status in event.get('outcomes',{}).items():
            if identity in e.nodes: e.nodes[identity].fl_status=status.upper()
        return {'accepted':True}
    app.include_router(router)
