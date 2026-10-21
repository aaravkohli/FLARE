"""Mesh research invariants: exact clocks, valid structure and isolated repeatability."""
from copy import deepcopy
import random
import pytest
from schemas.mission import MissionConfig, MissionProbe, ExperimentRequest, TOPOLOGIES
from simulation.swarm.engine import SwarmEngine
from simulation.swarm.experiments import reference_engine, summarize
from simulation.swarm.movement import Drone
from simulation.swarm.network import Link, PacketScheduler, shortest_path
from simulation.swarm.scenarios import DEMO_PRESETS
from simulation.swarm.topology import pair, topology_edges


def records():
    return [dict(drone_id=f'd{i}',display_name=f'UAV-{i}',index=i,mac=f'02:00:00:00:00:{i:02x}',access_port=i,enabled=True,rf_profile={}) for i in range(1,11)]

def engine(): return SwarmEngine(records(),isolated=True)

def graph(nodes):
    return {pair(a.id,b.id):Link(a.id,b.id,1,-50,30,12,0,8) for i,a in enumerate(nodes) for b in nodes[i+1:]}

@pytest.mark.parametrize('topology',['ring','tree','grid','chain'])
def test_failure_does_not_stitch_structural_holes(topology):
    nodes=[Drone(f'd{i}',f'U{i}',i,[i*10.,80.,0.],[0.,80.,0.]) for i in range(10)]
    eligible=graph(nodes); state={}
    before,_=topology_edges(topology,nodes,eligible,state=state)
    nodes[3].available=False
    after,_=topology_edges(topology,nodes,eligible,previous=before,state=state,now=1)
    assert after=={edge for edge in before if nodes[3].id not in edge}

def test_head_election_obeys_discovery_timeout():
    nodes=[Drone(f'd{i}',f'U{i}',i,[i*10.,80.,0.],[0.,80.,0.]) for i in range(10)]
    eligible=graph(nodes); state={}
    before,heads=topology_edges('star',nodes,eligible,state=state)
    next(d for d in nodes if d.id==heads[0]).available=False
    assert topology_edges('star',nodes,eligible,before,state,.2)[0]==set()
    edges,new_heads=topology_edges('star',nodes,eligible,before,state,.81)
    assert new_heads!=heads and len(edges)==8

def test_self_healing_adds_only_eligible_component_bridges():
    nodes=[Drone(f'd{i}',f'U{i}',i,[i%5*2.+(100 if i>=5 else 0),80.,0.],[0.,80.,0.]) for i in range(10)]
    eligible={e:l for e,l in graph(nodes).items() if (int(e[0][1:])<5)==(int(e[1][1:])<5) or e==pair('d4','d5')}
    sparse,_=topology_edges('partial_mesh',nodes,eligible)
    healed,_=topology_edges('self_healing_mesh',nodes,eligible)
    assert pair('d4','d5') not in sparse and pair('d4','d5') in healed
    assert healed<=eligible.keys()
    eligible.pop(pair('d4','d5'))
    assert not shortest_path(eligible,'d0','d9',{d.id:d for d in nodes})

def test_ground_is_an_endpoint_never_a_hidden_peer_relay():
    links={pair('a','base'):Link('a','base',1,-50,30,12,0,1),pair('b','base'):Link('b','base',1,-50,30,12,0,1)}
    assert shortest_path(links,'a','b',{})==[]
    assert shortest_path(links,'a','base',{})==['a','base']

def schedule_packet(partitions):
    events=[]; scheduler=PacketScheduler(dict(ttl_s=2,retries=2,capacity=10,queue_limit=10),random.Random(1),lambda k,d:events.append((k,d)))
    packet=scheduler.emit('a','c','sensor',512,['a','b','c'],0,0)
    links={pair('a','b'):Link('a','b',10,-50,30,12,0,8),pair('b','c'):Link('b','c',10,-50,30,12,0,8)}
    for t in partitions: scheduler.step(t,links,lambda *_:True)
    return scheduler,packet,events

def test_packet_timing_is_exact_and_independent_of_observation_ticks():
    a,p,events=schedule_packet([0,.05,.1])
    _,other,other_events=schedule_packet([0,.01,.02,.03,.1])
    assert p.completed_at==pytest.approx(2*(.008+512*8/12e6))
    assert p.completed_at==other.completed_at
    assert [(k,d['event_at']) for k,d in events]==[(k,d['event_at']) for k,d in other_events]
    assert a.generated==a.delivered+a.dropped+len(a.packets)

def test_shared_radio_contends_but_independent_radios_transmit_together():
    scheduler=PacketScheduler(dict(ttl_s=2,retries=0,capacity=10,queue_limit=10),random.Random(1),lambda *_:None)
    links={pair(a,b):Link(a,b,1,-50,30,1,0,1) for a,b in [('a','b'),('a','c'),('x','y')]}
    first=scheduler.emit('a','b','sensor',1000,['a','b'],0,0)
    second=scheduler.emit('a','c','sensor',1000,['a','c'],0,0)
    independent=scheduler.emit('x','y','sensor',1000,['x','y'],0,0)
    scheduler.step(.1,links,lambda *_:True)
    assert first.start_at==independent.start_at==0
    assert second.start_at==pytest.approx(.008)
    assert second.completed_at>first.completed_at

def test_direct_and_mesh_keep_shared_airtime_despite_separate_measurement_cohorts():
    scheduler=PacketScheduler(dict(ttl_s=2,retries=0,capacity=10,queue_limit=10),random.Random(1),lambda *_:None)
    links={pair(a,b):Link(a,b,1,-50,30,1,0,1) for a,b in [('a','base'),('a','c')]}
    direct=scheduler.emit('a','base','sensor',1000,['a','base'],0,0,family='direct')
    mesh=scheduler.emit('a','c','sensor',1000,['a','c'],0,0,family='mesh')
    satellite=scheduler.emit('a','base','sensor',1000,['a','base'],0,0,family='satellite')
    scheduler.step(.1,links,lambda *_:True)
    assert direct.start_at==satellite.start_at==0
    assert mesh.start_at==pytest.approx(.008)
    assert {row[1] for row in scheduler.channel_observations}=={'direct','mesh','satellite'}

def test_reliable_route_repair_preserves_completed_prefix():
    e=engine(); e.scheduler.cfg['jitter_ms']=0; ids=list(e.nodes); a,b,c,d,x=ids[:5]
    e.links={pair(u,v):Link(u,v,1,-50,30,12,0,1) for u,v in [(a,b),(b,c),(c,d),(b,x),(x,d)]}
    e.links[pair(b,x)].latency_ms=2
    e.flows.clear(); e._clear_routes()
    p=e.send(a,d,'fl_update');e.scheduler.step(.0014,e.packet_link,e.packet_allowed)
    assert p.hop==1 and p.history[0]['from_node']==a
    e.seconds=.0014;e.block_link(b,c)
    e.scheduler.step(.05,e.packet_link,e.packet_allowed,repair=e.repair_packet)
    e.scheduler.step(.1,e.packet_link,e.packet_allowed,repair=e.repair_packet)
    assert p.status=='delivered' and p.route==[a,b,x,d]
    assert [(h['from_node'],h['to_node']) for h in p.history]==[(a,b),(b,x),(x,d)]

def test_range_update_and_probe_revision_are_authoritative():
    e=engine(); e.configure(MissionConfig(expected_revision=e.revision,range_m=50,speed=.1))
    assert e.speed==.1 and all(n.range_m==50 for n in e.nodes.values())
    assert all(l.distance_m<=50 for l in e.links.values())
    request=MissionProbe(session_id=e.session_id,expected_revision=e.revision,source=list(e.nodes)[0],destination=list(e.nodes)[-1])
    assert e.probe(request)['packet']['family']=='mesh'
    with pytest.raises(ValueError): e.probe(request)
    for i in range(50005): e.packet_event('packet_retry',dict(id='p1',category='sensor',event_at=float(i)))
    assert len(e.packet_events)==50000 and e.packet_trace(1)['event_gap']

@pytest.mark.parametrize('scenario',DEMO_PRESETS)
def test_scenario_conditions_have_onset_recovery_and_no_invented_verdict(scenario):
    e=engine();e.scenario=scenario;e.playing=True
    e.seconds=8;e.scenario_step();e.seconds=25;e.scenario_step()
    if scenario not in ('normal','high_density'):
        assert e.scenario_actions=={'condition_onset','condition_recovery'}
    assert not e.jammers and not e.attacks.active and all(n.available for n in e.nodes.values())
    assert not e.security


def test_reference_inputs_and_metrics_repeat_without_touching_live_state(tmp_path,monkeypatch):
    sentinel=tmp_path/'byzantine.json';sentinel.write_text('operator state')
    monkeypatch.setenv('FLARE_BYZANTINE_STATE_PATH',str(sentinel))
    live=engine();config=deepcopy(live.cfg)
    request=ExperimentRequest(mode='compare',topologies=['partial_mesh','self_healing_mesh'],seeds=[7],count=10,duration_s=10,warmup_s=0)
    runs=[reference_engine(records(),config,request,t,7) for t in ['partial_mesh','self_healing_mesh','partial_mesh']]
    assert [n.position for n in runs[0].nodes.values()]==[n.position for n in runs[1].nodes.values()]
    results=[]
    for e in runs:
        cohort=[];e.trace_sink=lambda event:cohort.append(event['data']) if event['kind']=='packet_result' and event['data'].get('correlation_id')=='reference' else None
        e.playing=True
        for _ in range(600): e.tick(.05)
        results.append(summarize(e,request,e.topology,7,cohort))
        assert all('base' not in edge for edge in e.links)
        assert e.scheduler.generated==e.scheduler.delivered+e.scheduler.dropped
    assert results[0]==results[2]
    assert results[0]['generated']==results[1]['generated']==100
    assert sentinel.read_text()=='operator state' and live.seconds==0 and live.cfg==config


def test_repair_and_route_preview_cannot_bypass_external_policy_exclusions():
    from schemas.mission import RoutePolicy
    e=engine();e.scheduler.cfg['jitter_ms']=0
    a,b,c,d,x=list(e.nodes)[:5]
    e.links={pair(u,v):Link(u,v,1,-50,30,12,0,1) for u,v in [(a,b),(b,c),(c,d),(b,x),(x,d)]}
    e.flows.clear();e._clear_routes()
    policy=RoutePolicy(policy_id='restricted',session_id=e.session_id,revision=e.revision,source=a,destination=d,
        traffic_class='fl_update',family='mesh',hops=[a,b,c,d],restrictions={'excluded_nodes':[x]})
    assert e.install_policy(policy)['applied']
    p=e.send(a,d,'fl_update');e.scheduler.step(.0014,e.packet_link,e.packet_allowed)
    e.seconds=.0014;e.block_link(b,c)
    assert e.route(a,d)==[a,b,x,d]  # physically possible, prohibited by policy
    assert not e.inspect_route(a,d,'fl_update')['reachable']
    e.scheduler.step(.05,e.packet_link,e.packet_allowed,repair=e.repair_packet)
    assert p.status=='dropped' and x not in p.route


def test_self_healing_reconsiders_recovered_physical_links():
    e=engine();edge=next(edge for edge in e.links if 'base' not in edge)
    e.links.pop(edge);e.auto_blocked.add(edge);e.rebuild()
    assert edge not in e.auto_blocked
    assert any(event['kind']=='RESTORE' and event['data'].get('link')==list(edge) for event in e.events)


def test_seed_changes_are_pending_until_reset_and_never_relabel_active_draws():
    e=engine();active=e.seed
    state=e.configure(MissionConfig(expected_revision=e.revision,seed=7))
    assert state['seed']==active and state['pending_seed']==7 and e.scheduler.cfg['seed']==active
    e.reset()
    assert e.seed==e.cfg['seed']==e.scheduler.cfg['seed']==7 and e.pending_seed is None


def test_self_healing_bridges_data_components_without_using_control_only_relays():
    nodes=[Drone(f'd{i}',f'U{i}',i,[i%5*2.+(100 if i>=5 else 0),80.,0.],[0.,80.,0.]) for i in range(10)]
    eligible={edge:link for edge,link in graph(nodes).items() if (int(edge[0][1:])<5)==(int(edge[1][1:])<5) or edge in {pair('d4','d5'),pair('d3','d6')}}
    nodes[4].containment='control_only'
    healed,_=topology_edges('self_healing_mesh',nodes,eligible)
    assert pair('d3','d6') in healed
    route=shortest_path({edge:eligible[edge] for edge in healed},'d0','d9',{d.id:d for d in nodes})
    assert route and 'd4' not in route
