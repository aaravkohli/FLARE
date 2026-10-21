import type { CommunicationRoute, MissionSimulationState } from '../types.ts';
import type { SwarmSnapshot } from '../swarm.ts';

const interferenceProfiles = new Set(['spot','sweep','barrage','smart','reactive','adaptive']);
const families = new Set(['direct','satellite','mesh']);
/** Injection state comes from the mission, independently of slower detector observations. */
export function missionCommunicationConditions(snapshot: SwarmSnapshot, identity: string) {
  const conditions = snapshot.attacks?.active.filter(a => a.group === 'ew' && a.affected_targets.includes(identity)) ?? [];
  const jammedPaths = [...new Set(conditions.filter(a => interferenceProfiles.has(a.profile)).flatMap(a => a.effective_paths))]
    .filter((path): path is CommunicationRoute => families.has(path));
  return {conditions,jammedPaths};
}

export function withMissionConditions(state: MissionSimulationState, snapshot: SwarmSnapshot, identity: string, current: boolean) {
  if (!current || !snapshot.attacks || state.droneId !== identity) return state;
  const {conditions,jammedPaths} = missionCommunicationConditions(snapshot, identity);
  const ended = snapshot.attacks.history.filter(a => a.group === 'ew' && interferenceProfiles.has(a.profile)
    && (a.targets ?? a.affected_targets).includes(identity));
  return {...state, activeAttack: (conditions.find(a => a.profile !== 'fhss')?.profile ?? conditions[0]?.profile ?? 'none') as MissionSimulationState['activeAttack'],
    jammedPaths, routes: state.routes.map(route => ({...route,jammed:jammedPaths.includes(route.id),
      recoveryPending: route.jammed && !jammedPaths.includes(route.id) && ended.some(a => a.effective_paths.includes(route.id))}))};
}
