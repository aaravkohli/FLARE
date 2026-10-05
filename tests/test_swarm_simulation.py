"""Controlled packet simulation, no real RF/OpenFlow claims."""
from copy import deepcopy
import random
import pytest
from fastapi.testclient import TestClient
from fleet.registry import list_drones
from schemas.mission import MissionConfig, MissionControl, MissionCommand, FLTransfer, TOPOLOGIES, FORMATIONS
from schemas.decision_event import TelemetrySnapshot
from simulation.swarm.engine import SwarmEngine, RevisionConflict
from simulation.swarm.network import PacketScheduler, Link
from simulation.swarm.topology import pair
from simulation.swarm.movement import formation_positions


def engine(count=30):
    e=SwarmEngine(list_drones());e.configure(MissionConfig(expected_revision=e.revision,count=count));return e

def run(e,seconds):
    e.playing=True
    for _ in range(round(seconds/.05)):e.tick(.05)

@pytest.mark.parametrize('count',[10,20,30,50,75,100])
@pytest.mark.parametrize('topology',TOPOLOGIES)
def test_topology_matrix_and_live_changes(count,topology):
    e=engine(count);positions={n:d.position[:] for n,d in e.nodes.items()}
    e.configure(MissionConfig(expected_revision=e.revision,topology=topology))
    assert {n:d.position for n,d in e.nodes.items()}==positions
    assert len(e.nodes)==count
    assert all(all(n in e.nodes or n=='base' for n in edge) for edge in e.links)
    run(e,.1)
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped+len(e.scheduler.packets)
    for row in e.metrics().values():TelemetrySnapshot.model_validate(row)
    gateway=e.heads[0] if e.heads else next(iter(e.nodes))
    e.nodes[gateway].available=False;e.rebuild()
    assert all(gateway not in edge for edge in e.links)
    e.nodes[gateway].available=True;e.rebuild()
    e.resize(10)
    assert all(all(n in e.nodes or n=='base' for n in f['hops']) for f in e.flows.values())

@pytest.mark.parametrize('formation',FORMATIONS)
def test_formations_are_finite_and_movement_preserves_limits(formation):
    e=engine(10);before=deepcopy([d.position for d in e.nodes.values()])
    e.configure(MissionConfig(expected_revision=e.revision,formation=formation))
    assert [d.position for d in e.nodes.values()]==before
    assert len(formation_positions(formation,100))==100
    run(e,1)
    for d in e.nodes.values():
        assert sum(v*v for v in d.velocity)**.5<=16.001
        assert abs(d.velocity[1])<=2.001
        assert d.position[1]>=e.terrain.height(d.position[0],d.position[2])+34.9

def test_installed_rules_multihop_block_isolation_and_restore():
    e=engine(10);ids=list(e.nodes)[:3]
    # Controlled lossless graph proves actual three-hop forwarding independently of radio distribution.
    route=[*ids,'base'];e.links={pair(a,b):Link(a,b,20,-50,30,12,0,1) for a,b in zip(route,route[1:])}
    e.flows.clear();e._clear_routes();p=e.send(ids[0],'base','telemetry')
    assert p.route==route
    for t in range(10):e.scheduler.step(t*.05,e.packet_link,e.packet_allowed)
    assert p.status=='delivered' and e.scheduler.forwarded[ids[1]]==1
    p=e.send(ids[0],'base','telemetry');e.block_link(ids[1],ids[2]);e.scheduler.step(1,e.packet_link,e.packet_allowed)
    assert p.status=='dropped' and p.reason=='policy_or_link_removed'
    e.restore_link(ids[1],ids[2]);assert e.send(ids[0],'base','telemetry').route==route
    e.contain(ids[1],'quarantined','test');assert not e.route(ids[0],'base')
    e.contain(ids[1],'normal','recovery');assert e.route(ids[0],'base')==route

def test_satellite_is_separate_from_radio_and_hold_suspends_data():
    e=engine(10);identity=next(iter(e.nodes));e.links.clear();e.flows.clear();e._clear_routes()
    e.select_family(identity,'satellite');p=e.send(identity,'base','telemetry')
    for i in range(20):e.scheduler.step(i*.05,e.packet_link,e.packet_allowed)
    assert p.status=='delivered'
    e.select_family(identity,'hold');assert e.send(identity,'base','telemetry').reason=='no_route'

def test_pause_speed_reset_session_and_revision():
    e=engine();e.tick(.1);assert e.seconds==0
    for speed in [.25,.5,1,2,4]:
        e.configure(MissionConfig(expected_revision=e.revision,speed=speed));start=e.seconds;run(e,.2)
        assert e.seconds-start==pytest.approx(.2*speed)
    session=e.session_id;e.reset();assert e.session_id!=session and not e.playing and e.seconds==0
    with pytest.raises(RevisionConflict):e.configure(MissionConfig(expected_revision=0,count=50))
    with pytest.raises(RevisionConflict):e.submit_transfer(FLTransfer(client_id=next(iter(e.nodes)),round=1,digest='a'*64,size_bytes=1,session_id=session))

def test_commands_need_delivery_ack_and_lost_control_expires():
    e=engine(10);identity=next(iter(e.nodes))
    e.playing=True
    e.links={pair('base',identity):Link('base',identity,10,-50,30,12,0,1)};e.flows.clear();e._clear_routes()
    request=MissionCommand(command_id='c1',targets=[identity],action='hold')
    record=e.command(request);assert record['outcomes'][identity]['status']=='pending'
    for i in range(20):e.seconds=i*.05;e.scheduler.step(e.seconds,e.packet_link,e.packet_allowed)
    assert e.commands['c1']['outcomes'][identity]['status']=='acknowledged'
    assert e.nodes[identity].mode=='hold'
    e.controller_connected=False;e.command(MissionCommand(command_id='c2',targets=[identity],action='resume',ttl_s=1))
    e.seconds+=2;e.expire_commands();e.controller_connected=True
    assert e.commands['c2']['outcomes'][identity]['status']=='expired' and e.nodes[identity].mode=='hold'

def test_transfer_chunks_conservation_and_failed_delivery():
    e=engine(10);identity=next(iter(e.nodes));e.select_family(identity,'satellite')
    receipt=e.submit_transfer(FLTransfer(client_id=identity,round=1,digest='a'*64,size_bytes=2_000_000,session_id=e.session_id))
    for i in range(1000):e.seconds=i*.05;e.scheduler.step(e.seconds,e.packet_link,e.packet_allowed);e.refresh_transfers()
    assert e.transfers[receipt['transfer_id']]['status'] in ('delivered','delivery_failed')
    assert e.transfers[receipt['transfer_id']]['pending']==0
    assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped
    e.select_family(identity,'hold')
    failed=e.submit_transfer(FLTransfer(client_id=identity,round=2,digest='b'*64,size_bytes=32,session_id=e.session_id));e.refresh_transfers()
    assert e.transfers[failed['transfer_id']]['status']=='delivery_failed'

def test_expiring_inflight_releases_queue():
    scheduler=PacketScheduler({'ttl_s':.1,'retries':3,'capacity':10,'queue_limit':1},random.Random(7),lambda *_:None)
    links={pair('a','b'):Link('a','b',1,-50,30,.001,0,1)}
    p=scheduler.emit('a','b','telemetry',1000,['a','b'],0,1);scheduler.step(0,links,lambda *_:True)
    scheduler.step(.2,links,lambda *_:True)
    assert p.reason=='expired' and scheduler.queued[pair('a','b')]==0
    assert scheduler.busy[pair('a','b')]==.2

def test_bounded_queue_retries_and_packet_conservation():
    events=[]
    scheduler=PacketScheduler({'ttl_s':10,'retries':2,'capacity':10,'queue_limit':1},random.Random(42),lambda kind,data:events.append((kind,data)))
    link=Link('a','b',1,-50,30,1,1,1);links={pair('a','b'):link}
    reliable=scheduler.emit('a','b','fl_update',100,['a','b'],0,1)
    overflow=scheduler.emit('a','b','telemetry',100,['a','b'],0,1)
    scheduler.step(0,links,lambda *_:True)
    assert overflow.reason=='queue_overflow'
    for t in (.1,.2,.3,.4,.5):scheduler.step(t,links,lambda *_:True)
    assert reliable.reason=='radio_loss' and reliable.retries==2
    assert scheduler.transmitted['a']==3 and scheduler.queued[pair('a','b')]==0
    assert scheduler.generated==scheduler.delivered+scheduler.dropped+len(scheduler.packets)==2
    assert len([e for e in events if e[0]=='packet_result'])==2

def test_simulator_service_token_and_lifecycle(monkeypatch,tmp_path):
    from simulation.swarm.session import write_registry
    registry=tmp_path/'mission.yaml';write_registry(registry,list_drones())
    monkeypatch.setenv('FLARE_SWARM_ENABLED','1');monkeypatch.setenv('FLARE_FLEET_REGISTRY',str(registry))
    from sdn.mock_sdn import app,SDN_API_TOKEN
    with TestClient(app) as client:
        assert client.get('/simulation/state').status_code==401
        headers={'Authorization':'Bearer '+SDN_API_TOKEN};s=client.get('/simulation/state',headers=headers).json()
        assert len(s['nodes'])==30 and s['evidence']=='packet_simulation'
        response=client.post('/simulation/control',headers=headers,json={'action':'play','expected_revision':s['revision']})
        assert response.status_code==200 and response.json()['playing']
        assert client.patch('/simulation/config',headers=headers,json={'count':50,'expected_revision':s['revision']}).status_code==409

def test_shared_dqn_batch_scalar_equivalence(tmp_path):
    from pathlib import Path
    from rl.agent import RLAgent

    checkpoint_path = Path('models/rl_model_v3.zip')
    if not (
        checkpoint_path.is_file()
        and checkpoint_path.with_name('rl_model_v3.zip.metadata.json').is_file()
    ):
        from rl.checkpoint import ROUTING_STATE_V3, write_checkpoint_metadata
        from rl.env import TrustAwareDronePathEnv
        from stable_baselines3 import DQN

        checkpoint_path = tmp_path / 'rl_model_v3.zip'
        env = TrustAwareDronePathEnv()
        DQN('MlpPolicy', env).save(checkpoint_path)
        write_checkpoint_metadata(
            checkpoint_path,
            algorithm='dqn',
            observation_dim=18,
            action_count=4,
            max_episode_steps=500,
            training_timesteps=1,
            training_mode='synthetic',
            training_seed=42,
            observation_definition=ROUTING_STATE_V3,
        )

    model_target = str(checkpoint_path)
    agents = [RLAgent(model_target, routing_contract_name='routing_state_v3') for _ in range(3)]
    scalar = [RLAgent(model_target, routing_contract_name='routing_state_v3') for _ in range(3)]
    assert agents[0].model is agents[1].model
    inputs = [dict(path_scores=[.1, .2, .3], reward=i * .1, client_trust=1 - i * .2) for i in range(3)]
    for _ in range(3):
        batch = RLAgent.predict_batch(agents, inputs)
        assert batch == [a.predict(**row) for a, row in zip(scalar, inputs)]

def test_failed_install_keeps_only_valid_confirmed_route(monkeypatch):
    e=engine(10);source=next(iter(e.nodes));e.select_family(source,'satellite')
    old=deepcopy(e.flows[source+':base:telemetry'])
    monkeypatch.setattr(e,'install_policy',lambda _: {'status':'failed','applied':False,'reason':'injected_adapter_failure'})
    result=e.select_family(source,'direct')
    assert result['installed_path']=='satellite' and e.flows[source+':base:telemetry']==old
    e.failed_families.add('satellite');e.rebuild()
    assert not e.ensure_route(source,'base','telemetry','satellite')

def test_ryu_adapter_rejects_arbitrary_hops_without_calling_controller():
    from sdn.enforcement import RyuOpenFlowAdapter
    from schemas.mission import RoutePolicy
    class Controller:
        def apply_routing_decision(self,*_):raise AssertionError('unsupported route must not reach controller')
    result=RyuOpenFlowAdapter(Controller()).install_flow(RoutePolicy(policy_id='test',session_id='session',revision=1,source='drone_1',family='mesh',hops=['drone_1','drone_2','base']))
    assert result['status']=='unsupported' and not result['applied']

def test_hold_is_a_persisted_policy_and_command_sessions_are_checked():
    from schemas.mission import RoutePolicy
    e=engine(10);identity=next(iter(e.nodes))
    result=e.install_policy(RoutePolicy(policy_id='hold',session_id=e.session_id,revision=e.revision,source=identity,family='hold',hops=[]))
    assert result['applied'] and e.flows['hold']['family']=='hold'
    e.rebuild();assert 'hold' in e.flows
    assert e.send(identity,'base','telemetry').reason=='no_route'
    assert e.metrics()[identity]['paths'][1]['pdr']==.98
    e.select_family(identity,'satellite')
    assert not any(f['family']=='hold' and f['source']==identity for f in e.flows.values())
    with pytest.raises(RevisionConflict):e.command(MissionCommand(command_id='old',session_id='old',expected_revision=e.revision,targets=[identity],action='resume'))

def test_waypoints_validate_and_transition_after_radio_delivery():
    e=engine(10);identity=next(iter(e.nodes));d=e.nodes[identity]
    e.playing=True
    e.links={pair('base',identity):Link('base',identity,10,-50,30,12,0,1)};e.flows.clear();e._clear_routes()
    points=[[d.position[0]+30,d.position[1],d.position[2]]]
    e.command(MissionCommand(command_id='waypoint',targets=[identity],action='waypoints',parameters={'points':points}))
    assert not d.waypoints
    for i in range(10):e.seconds=i*.05;e.scheduler.step(e.seconds,e.packet_link,e.packet_allowed)
    assert d.waypoints==points and d.mode=='waypoints' and e.commands['waypoint']['outcomes'][identity]['status']=='acknowledged'
    with pytest.raises(ValueError):e.command(MissionCommand(command_id='bad',targets=[identity],action='waypoints',parameters={'points':[[0,float('inf'),0]]}))

def test_external_policy_id_controls_actual_forwarding_and_restrictions():
    from schemas.mission import RoutePolicy
    e=engine(10);identity=next(iter(e.nodes));e.links={pair(identity,'base'):Link(identity,'base',10,-50,30,12,0,1)};e.flows.clear();e._clear_routes()
    policy=RoutePolicy(policy_id='operator-rule',source=identity,session_id=e.session_id,revision=e.revision,family='direct',hops=[identity,'base'])
    assert e.install_policy(policy)['applied']
    p=e.send(identity,'base','telemetry');assert e.installed_rule(identity,'base','telemetry')['policy_id']=='operator-rule'
    e.scheduler.step(0,e.packet_link,e.packet_allowed);e.scheduler.step(.1,e.packet_link,e.packet_allowed);assert p.status=='delivered'
    forbidden=policy.model_copy(update={'restrictions':{'excluded_nodes':[identity]}})
    assert not e.install_policy(forbidden)['applied']
    e.nodes[identity].health=0;assert not e.route(identity,'base','satellite')

def test_poisoning_arms_next_actual_dispatch_before_training(monkeypatch,tmp_path):
    from simulation.byzantine_state import get_attack_mode
    monkeypatch.setenv('FLARE_BYZANTINE_STATE_PATH',str(tmp_path/'attacks.json'))
    e=engine(10);identities=list(e.nodes)
    e.load_scenario('poisoning',e.revision)
    e.fl_dispatch(1,identities[:5]);assert e.poisoning_client is None
    e.seconds=8;e.scenario_step();assert e.poisoning_client is None
    e.fl_dispatch(2,identities[5:10])
    assert e.poisoning_client==identities[5] and get_attack_mode(identities[5])=='poisoned'
    e.fl_dispatch(3,identities[:5]);assert get_attack_mode(identities[0])=='normal'
    with pytest.raises(ValueError):e.fl_dispatch(4,['missing'])
    e.reset();assert get_attack_mode(identities[5])=='normal' and e.poisoning_client is None
