"""Authenticated simulator service; fixed-step time is independent of browsers."""
from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import time
from fastapi import APIRouter, Depends, HTTPException
from schemas.mission import MissionConfig, MissionControl, MissionCommand, FLTransfer, RoutePolicy, MissionAttack, ClearMissionAttacks
from simulation.swarm.engine import SwarmEngine, RevisionConflict, SCENARIOS
from sdn.enforcement import SimulationSDNAdapter
from fleet.registry import list_drones


def attach_simulation(app, authenticate):
    app.state.swarm = None
    @asynccontextmanager
    async def lifespan(application):
        task = None
        if os.getenv('FLARE_SWARM_ENABLED') == '1':
            registry = Path(os.environ['FLARE_FLEET_REGISTRY'])
            if registry.resolve() == Path('config/fleet_registry.yaml').resolve():
                raise RuntimeError('swarm requires a separate mission registry')
            application.state.swarm = SwarmEngine(list_drones(), registry_path=registry)
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
            application.state.swarm = None
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
            'eligible_clients':[n.id for n in e.nodes.values() if n.available and n.health>0 and n.containment=='normal']}
    @router.get('/state')
    async def state(after: int = 0): return engine().snapshot(after)
    @router.get('/metrics')
    async def metrics():
        e=engine()
        return {'session_id':e.session_id,'simulation_time':e.seconds,'playing':e.playing,'metrics':e.metrics()}
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
