export const COMMUNICATION_PROFILES = [
  ['none', 'Normal'], ['spot', 'Spot jam'], ['sweep', 'Sweep'], ['barrage', 'Barrage'],
  ['smart', 'Smart jam'], ['reactive', 'Reactive'], ['adaptive', 'Adaptive'], ['fhss', 'FHSS'],
  ['spoofing', 'Spoofing'], ['gps_spoofing', 'GPS spoofing'], ['replay', 'Replay'], ['dos', 'DoS'],
] as const;
export const ATTACK_GROUPS = {ew: 'Communication', traffic: 'Insider traffic', fl: 'FL poisoning'} as const;
export type AttackGroup = keyof typeof ATTACK_GROUPS;
export type AttackScope = 'inspected' | 'selected' | 'fleet';
export const FALLBACK_PROFILES: Record<AttackGroup, readonly string[]> = {
  ew: COMMUNICATION_PROFILES.map(([id]) => id),
  traffic: ['normal', 'selective_forwarding', 'telemetry_falsification', 'control_flood', 'replay'], fl: ['normal', 'poisoned'],
};
export const profileLabel = (id: string) => COMMUNICATION_PROFILES.find(([key]) => key === id)?.[1]
  ?? ({normal: 'Normal', poisoned: 'Model poisoning', selective_forwarding: 'Selective forwarding',
    telemetry_falsification: 'Telemetry falsification', control_flood: 'Control flood'} as Record<string,string>)[id] ?? id;
export const pathlessProfile = (group: AttackGroup, profile: string) => group !== 'ew' || ['none', 'barrage', 'sweep', 'gps_spoofing'].includes(profile);
export function attackTargets(scope: AttackScope, inspected: string, selected: string[], members: string[]) {
  const valid = new Set(members);
  return [...new Set(scope === 'fleet' ? members : scope === 'selected' ? selected : [inspected])].filter(id => valid.has(id));
}
export function attackAction(group: AttackGroup, profile: string) {
  if (profile === 'none' || profile === 'normal') return `Clear ${group === 'ew' ? 'communication' : group === 'fl' ? 'poisoning' : 'traffic'} conditions`;
  return ({spot: 'Inject spot jam', smart: 'Run smart jam', fhss: 'Run FHSS mitigation', barrage: 'Start barrage jam', sweep: 'Start sweep jam', poisoned: 'Arm model poisoning'} as Record<string,string>)[profile] ?? `Start ${profileLabel(profile).toLowerCase()}`;
}
