"""Finite pairwise radio/formation/topology/clock interactions, not a continuous sweep."""
from itertools import product,combinations
import pytest
from fleet.registry import list_drones
from schemas.mission import MissionConfig,TOPOLOGIES,LEGACY_TOPOLOGIES,FORMATIONS
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.scenarios import DEMO_PRESETS

FACTORS=[TOPOLOGIES+LEGACY_TOPOLOGIES,FORMATIONS,(.1,.25,.5,1.,2.,4.),(100.,350.,1000.),(7,42,99)]

def pairs(case):
    return {(i,case[i],j,case[j]) for i,j in combinations(range(len(case)),2)}

def covering_cases():
    candidates=list(product(*FACTORS));uncovered=set().union(*(pairs(c) for c in candidates));chosen=[]
    while uncovered:
        best=max(candidates,key=lambda c:len(pairs(c)&uncovered))
        chosen.append(best);uncovered-=pairs(best)
    return chosen

CASES=covering_cases()

def test_pairwise_schedule_covers_every_discrete_factor_pair():
    assert set().union(*(pairs(c) for c in CASES))==set().union(*(pairs(c) for c in product(*FACTORS)))

@pytest.mark.parametrize('topology,formation,speed,radio_range,seed',CASES)
def test_pairwise_configurations_keep_clock_physics_and_conservation(topology,formation,speed,radio_range,seed):
    e=SwarmEngine(list_drones(),isolated=True)
    e.configure(MissionConfig(expected_revision=e.revision,count=10,topology=topology,formation=formation,speed=speed,range_m=radio_range,seed=seed));e.reset();e.playing=True
    for _ in range(4):e.tick(.25)
    assert e.seconds==pytest.approx(speed)
    assert all(l.distance_m<=radio_range for l in e.links.values())
    ids=list(e.nodes)
    assert 'base' not in e.route(ids[0],ids[-1])[1:-1]
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)

@pytest.mark.parametrize('scenario',DEMO_PRESETS)
def test_each_scenario_runs_through_actual_clock_onset_and_recovery(scenario):
    e=SwarmEngine(list_drones(),isolated=True);e.load_scenario(scenario,e.revision);e.playing=True
    for _ in range(104):e.tick(.25)
    assert e.seconds==pytest.approx(26)
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)
    if scenario not in ('normal','high_density'):assert {'condition_onset','condition_recovery'}<=e.scenario_actions
    assert not e.jammers
    assert not any(a['attack_id'].startswith('demo-') for a in e.attacks.active.values())
