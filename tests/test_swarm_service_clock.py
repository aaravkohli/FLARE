"""Catch-up work yields to real HTTP reads/controls without changing packet time."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
import httpx
import pytest
import yaml

from schemas.mission import MissionControl
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.service import attach_simulation, simulation_clock


def scientific_state(e):
    return dict(seconds=e.seconds,accumulator=e.accumulator,topology=e.topology,
                nodes={n:asdict(d) for n,d in e.nodes.items()},
                packets=[p.snapshot() for p in e.scheduler.packets.values()],
                results=list(e.packet_results),
                packet_events=[{k:v for k,v in row.items() if k not in ('session_id','timestamp')} for row in e.packet_events],
                counts=(e.scheduler.generated,e.scheduler.delivered,e.scheduler.dropped),
                autonomy=e.autonomy.snapshot(),random=e.rng.getstate())


@pytest.mark.parametrize('speed',[.1,.25,.5,1,2,4])
@pytest.mark.parametrize('autonomy',['off','recovery_v1'])
def test_bounded_ticks_preserve_budget_and_exact_packet_outcomes(speed,autonomy):
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg.update(default_count=10,topology_mode='manual',topology='partial_mesh',autonomy_mode=autonomy)
    reference=SwarmEngine([],cfg,isolated=True)
    bounded=SwarmEngine([],deepcopy(cfg),isolated=True)
    reference.speed=bounded.speed=speed
    reference.playing=bounded.playing=True
    # Include a clipped wall delta and fractional residuals; admit each exactly
    # once. Draining must never add more wall time or truncate fixed steps.
    for wall in (.025,.25,.5,.03,.1,.23):
        reference.tick(wall)
        bounded.tick(wall,max_steps=1)
        while bounded.accumulator+1e-9>=bounded.cfg['fixed_step_s']:
            bounded.tick(0,max_steps=1)
        assert scientific_state(bounded)==scientific_state(reference)
        assert bounded.performance.clipped==reference.performance.clipped
        assert bounded.scheduler.generated==bounded.scheduler.delivered+bounded.scheduler.dropped+len(bounded.scheduler.packets)


def test_pause_or_reset_cancels_pending_catchup_without_applying_old_budget():
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg.update(default_count=10,topology_mode='manual')
    e=SwarmEngine([],cfg,isolated=True);e.speed=4;e.playing=True
    e.tick(.25,max_steps=1)
    assert e.seconds==.05 and e.accumulator==pytest.approx(.95)
    e.control(MissionControl(action='pause',expected_revision=e.revision))
    e.tick(0,max_steps=1)
    assert not e.playing and e.seconds==.05 and e.accumulator==0
    e.playing=True;e.tick(.25,max_steps=1)
    assert e.accumulator>0
    e.control(MissionControl(action='reset',expected_revision=e.revision))
    e.tick(0,max_steps=1)
    assert not e.playing and e.seconds==0 and e.accumulator==0


def test_thirty_uav_total_loss_at_four_times_speed_matches_unbounded_execution():
    cfg=yaml.safe_load(Path('config/swarm_config.yaml').read_text())
    cfg.update(autonomy_mode='recovery_v1')
    reference=SwarmEngine([],cfg,isolated=True)
    bounded=SwarmEngine([],deepcopy(cfg),isolated=True)
    for e in (reference,bounded):
        e.load_scenario('recovery_total_loss',e.revision)
        e.speed=4;e.playing=True
        assert len(e.nodes)==30
    for _ in range(14):
        reference.tick(.25)
        bounded.tick(.25,max_steps=1)
        while bounded.accumulator+1e-9>=bounded.cfg['fixed_step_s']:
            bounded.tick(0,max_steps=1)
    assert scientific_state(bounded)==scientific_state(reference)
    assert sum(s.latched for s in bounded.flight_safety.supervisors.values())>=5
    assert bounded.scheduler.generated==bounded.scheduler.delivered+bounded.scheduler.dropped+len(bounded.scheduler.packets)


def test_http_state_and_pause_run_between_catchup_steps():
    async def run():
        first=asyncio.Event()
        class BusyEngine:
            cfg={'fixed_step_s':.05}
            playing=True
            accumulator=1.
            seconds=0.
            revision=1
            def tick(self,wall_delta,*,max_steps=None):
                if not self.playing:return
                completed=0
                while self.accumulator+1e-9>=.05 and (max_steps is None or completed<max_steps):
                    self.seconds+=.05;self.accumulator-=.05;completed+=1
                first.set()
            def snapshot(self,after=0):
                return dict(simulation_time=self.seconds,playing=self.playing,revision=self.revision)
            def control(self,request):
                assert request.expected_revision==self.revision and request.action=='pause'
                self.playing=False;self.accumulator=0;self.revision+=1
                return self.snapshot()
        async def authenticate():pass
        app=FastAPI();attach_simulation(app,authenticate);e=BusyEngine();app.state.swarm=e
        task=asyncio.create_task(simulation_clock(app))
        try:
            await asyncio.wait_for(first.wait(),1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.get('/simulation/state')
                assert response.status_code==200
                # The old all-steps batch reached 1.0 before serving this read.
                assert response.json()['simulation_time']<1
                before=e.seconds
                paused=await client.post('/simulation/control',json=dict(action='pause',expected_revision=1))
                assert paused.status_code==200 and not paused.json()['playing']
                await asyncio.sleep(.04)
                assert e.seconds==before and e.accumulator==0
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
