"""Complete finite topology/size/seed graph matrix and timed scenario actions."""
import math
import pytest
from fleet.registry import list_drones
from schemas.mission import MissionConfig,TOPOLOGIES
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.scenarios import DEMO_PRESETS,schedule

@pytest.mark.parametrize('seed',[7,42,99])
@pytest.mark.parametrize('count',range(10,101,10))
@pytest.mark.parametrize('topology',TOPOLOGIES)
def test_complete_topology_size_seed_initialization_matrix(topology,count,seed):
    e=SwarmEngine(list_drones(),isolated=True)
    e.configure(MissionConfig(expected_revision=e.revision,count=count,topology=topology,seed=seed));e.reset()
    assert len(e.nodes)==count and e.seed==seed
    assert all(math.isfinite(v) for n in e.nodes.values() for v in n.position)
    assert all(l.distance_m<=min([e.nodes[n].range_m for n in edge if n!='base'])+1e-8 for edge,l in e.links.items())
    positions={n:d.position[:] for n,d in e.nodes.items()}
    e.configure(MissionConfig(expected_revision=e.revision,topology=topology))
    assert positions=={n:d.position for n,d in e.nodes.items()}
    before={edge for edge in e.links if 'base' not in edge};failed=list(e.nodes)[3]
    e.nodes[failed].available=False;e.rebuild()
    assert all(failed not in edge for edge in e.links)
    if topology in ('ring','tree','grid'):assert {edge for edge in e.links if 'base' not in edge}=={edge for edge in before if failed not in edge}
    e.playing=True;e.tick(.05)
    assert e.seconds==pytest.approx(.05)
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)

@pytest.mark.parametrize('scenario',DEMO_PRESETS)
def test_demonstration_onset_and_recovery_are_timed_actions_not_detector_verdicts(scenario):
    e=SwarmEngine(list_drones(),isolated=True);e.load_scenario(scenario,e.revision)
    e.seconds=7.95;e.scenario_step();assert not e.scenario_actions
    e.seconds=8.;e.scenario_step()
    if schedule(scenario):assert 'condition_onset' in e.scenario_actions
    assert not e.security
    e.seconds=25.;e.scenario_step();e.attacks.step(.05)
    if schedule(scenario):assert 'condition_recovery' in e.scenario_actions
    assert not e.security
    assert not e.jammers
    assert not any(a['attack_id'].startswith('demo-') for a in e.attacks.active.values())
