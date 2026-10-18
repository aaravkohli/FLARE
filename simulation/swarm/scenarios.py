"""Reproducible condition schedules. Inject conditions, never detector verdicts."""
from schemas.mission import MissionAttack
from simulation.swarm.topology import pair

DEMO_PRESETS={
    'normal':dict(count=30,topology='self_healing_mesh',formation='distributed'),
    'high_density':dict(count=100,topology='self_healing_mesh',formation='distributed'),
    'mobility':dict(count=30,topology='partial_mesh',formation='grid'),
    'single_failure':dict(count=30,topology='star',formation='distributed'),
    'link_failures':dict(count=30,topology='hybrid_mesh',formation='grid'),
    'regional_jamming':dict(count=30,topology='self_healing_mesh',formation='grid'),
    'compromised':dict(count=30,topology='hybrid_mesh',formation='cluster'),
    'self_healing':dict(count=30,topology='self_healing_mesh',formation='grid'),
    'congestion':dict(count=30,topology='partial_mesh',formation='distributed'),
    'partition_reconnection':dict(count=30,topology='self_healing_mesh',formation='grid'),
}

def schedule(name):
    if name not in DEMO_PRESETS: raise ValueError('unknown demonstration scenario')
    return [] if name in ('normal','high_density') else [dict(at=8.,action='condition_onset',condition=name),dict(at=25.,action='condition_recovery',condition=name)]

def step_scenario(e):
    if e.scenario not in DEMO_PRESETS: return False
    nodes=list(e.nodes.values()); ids=[d.id for d in nodes]
    for action in schedule(e.scenario):
        key=action['action']
        if e.seconds+1e-9<action['at'] or key in e.scenario_actions: continue
        e.scenario_actions.add(key); onset=key=='condition_onset'; name=e.scenario
        e.event('NETWORK',key,dict(action,detector_verdict='awaiting_observation'))
        if name in ('single_failure','self_healing'):
            target=nodes[0] if name=='single_failure' else nodes[min(3,len(nodes)-1)]
            target.available=not onset; target.health=0. if onset else 1.
        elif name in ('link_failures','partition_reconnection'):
            if onset:
                if name=='partition_reconnection':
                    middle=len(ids)//2; cut={pair(a,b) for a in ids[:middle] for b in ids[middle:]}
                else: cut={pair(ids[i],ids[i+1]) for i in range(0,min(8,len(ids)-1),2)}
                e.scenario_blocked=cut-e.blocked
                e.blocked.update(cut)
            else: e.blocked.difference_update(getattr(e,'scenario_blocked',set()))
        elif name=='regional_jamming':
            e.jammers=[dict(position=[0.,80.,0.],radius_m=125.,power_dbm=-12.)] if onset else []
        elif name=='mobility':
            for i,d in enumerate(nodes):
                d.mode='waypoints' if onset else 'patrol'
                d.waypoints=[[d.position[0]+(-240 if i%2 else 240),d.position[1],d.position[2]]]
                d.waypoint_index=0
        elif name in ('compromised','congestion'):
            attack_id='demo-'+name
            if onset:
                request=MissionAttack(session_id=e.session_id,expected_revision=e.revision,attack_id=attack_id,
                    group='traffic',profile='selective_forwarding' if name=='compromised' else 'control_flood',
                    targets=[ids[min(3,len(ids)-1)]],paths=['mesh'],duration_s=17)
                e.attacks.inject(request)
            else: e.attacks.clear(attack_id=attack_id)
        e.rebuild()
    return True
