"""Seeded simulator fixtures; none are detector classifications or sensor claims."""
NAMES=('direct_jam','satellite_switch_failure','dual_interference','mesh_transition_loss','total_loss',
       'intermittent','switch_instability','multiple_isolation','low_battery_isolation','failed_recovery_landing')
PRESETS={'recovery_'+name:dict(count=30,topology='partial_mesh',formation='distributed') for name in NAMES}


def schedule(name):
    if name not in PRESETS:raise ValueError('unknown recovery scenario')
    condition=name.removeprefix('recovery_')
    events=[dict(at=8.,action='onset',condition=condition)]
    if condition in ('direct_jam','satellite_switch_failure','dual_interference','mesh_transition_loss','switch_instability','multiple_isolation'):
        events.append(dict(at=25.,action='restore',condition=condition))
    if condition=='intermittent':
        events += [dict(at=t,action='restore' if i%2==0 else 'onset',condition=condition) for i,t in enumerate((15.,17.,20.,22.,25.,27.,35.))]
    return events


def step(e):
    if e.scenario not in PRESETS:return False
    from schemas.mission import MissionAttack
    ids=list(e.nodes)
    for i,action in enumerate(schedule(e.scenario)):
        key='recovery_action_'+str(i)
        if e.seconds+1e-9<action['at'] or key in e.scenario_actions:continue
        e.scenario_actions.add(key);condition=action['condition'];on=action['action']=='onset'
        targets=ids[:5] if condition in ('multiple_isolation','total_loss') else ids[:1]
        e.event('SCENARIO','recovery_condition',dict(**action,targets=targets,evidence='explicit_fault_fixture',detector_verdict='not_injected'))
        if condition in ('direct_jam','dual_interference'):
            if on:
                e.attacks.inject(MissionAttack(session_id=e.session_id,expected_revision=e.revision,attack_id='recovery-interference',group='ew',profile='spot',targets=targets,paths=['direct']+(['satellite'] if condition=='dual_interference' else []),duration_s=17))
            else:e.attacks.clear(attack_id='recovery-interference')
        else:
            families=['satellite'] if condition=='satellite_switch_failure' else ['mesh'] if condition=='mesh_transition_loss' else ['direct','satellite','mesh']
            for n in targets:
                for f in families:e.autonomy.interfaces.setdefault(n,{})[f]=not on
            # Fault fixtures apply in both arms; autonomy-off retains its flight
            # behavior while the same communication hardware is unavailable.
            if condition=='low_battery_isolation' and on:e.nodes[targets[0]].battery=.08
            if condition in ('satellite_switch_failure','mesh_transition_loss','switch_instability') and on:
                try:e.adaptive.request('ring' if e.topology!='ring' else 'tree','Recovery validation transition',False)
                except ValueError as exc:e.event('RECOVERY','transition_rejected',dict(reason=str(exc)))
        e.rebuild()
    if e.scenario=='recovery_switch_instability':
        for at in (10,12,14,16,18):
            key='repeated_switch_'+str(at)
            if e.seconds+1e-9>=at and key not in e.scenario_actions:
                e.scenario_actions.add(key)
                try:e.adaptive.request('ring' if at%4 else 'tree','Operator instability fixture')
                except ValueError as exc:e.event('RECOVERY','transition_rejected',dict(reason=str(exc)))
    return True
