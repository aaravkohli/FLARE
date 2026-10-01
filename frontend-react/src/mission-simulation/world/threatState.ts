import type { AttackProfile, CommunicationRoute, MissionSimulationState } from '../types.ts';
export interface ThreatVisual {
  profile: AttackProfile; paths: CommunicationRoute[]; drift: number | null; label: string; sceneNote: string;
}
export function threatVisual(state: MissionSimulationState, selectedId: string): ThreatVisual {
  const profile = state.droneId === selectedId ? state.activeAttack : 'none';
  const drift = profile === 'gps_spoofing' && Number.isFinite(state.gps?.drift_m) && state.gps!.drift_m > 0 ? state.gps!.drift_m : null;
  const detail: Record<AttackProfile, string> = {
    none: 'No active attack', spot: 'Simulated RF interference · spot affected area schematic, source unlocated',
    sweep: 'Simulated RF interference · sweep direction schematic, source unlocated',
    barrage: 'Simulated RF interference · barrage affected area schematic, source unlocated',
    smart: 'Smart interference · reported affected links',
    reactive: 'Reactive interference · reported affected links', adaptive: 'Adaptive interference · reported affected links',
    fhss: 'Frequency-hopping mitigation · simulated', spoofing: 'Reported telemetry spoofing',
    gps_spoofing: drift === null ? 'GPS spoofing · reported drift unavailable; no trusted position reference'
      : `Reported GPS drift ${drift.toFixed(1)} m · display direction and spacing schematic; no trusted position reference`,
    replay: 'Repeated telemetry · flight position and age not established', dos: 'DoS · illustrative congestion, not measured traffic volume',
  };
  const sceneNotes: Record<AttackProfile, string> = {
    none: '', spot: 'SIMULATED RF INTERFERENCE\nSPOT · AFFECTED AREA SCHEMATIC',
    barrage: 'SIMULATED RF INTERFERENCE\nBARRAGE · AFFECTED AREA SCHEMATIC',
    sweep: 'SIMULATED RF INTERFERENCE\nSWEEP · DIRECTION SCHEMATIC',
    smart: 'REPORTED AFFECTED LINKS · SCHEMATIC', reactive: 'REPORTED AFFECTED LINKS · SCHEMATIC',
    adaptive: 'REPORTED AFFECTED LINKS · SCHEMATIC', fhss: 'SIMULATED FREQUENCY HOPPING',
    spoofing: 'REPORTED TELEMETRY SPOOFING · NO SOURCE LOCATION',
    gps_spoofing: '● PHYSICAL UAV / ◇ REPORTED GPS\nDIRECTION SCHEMATIC · REFERENCE UNTRUSTED',
    replay: 'REPLAY INDICATOR · FRESH POSITION UNAVAILABLE',
    dos: 'DOS · ILLUSTRATIVE QUEUE\nNOT MEASURED PACKET VOLUME',
  };
  return { profile, paths: profile === 'none' ? [] : [...state.jammedPaths], drift,
    label: detail[profile], sceneNote: sceneNotes[profile] };
}
/** Queue marks require a reported affected path that is also currently forwarding. */
export function threatQueuePath(visual: ThreatVisual, forwardingPath: 'direct' | 'satellite' | null): 'direct' | 'satellite' | null {
  return visual.profile === 'dos' && forwardingPath && visual.paths.includes(forwardingPath) ? forwardingPath : null;
}
/** Only a previously active visual can decay. A new none state creates nothing. */
export function threatOpacity(previous: number, active: boolean, dt: number): number {
  return active ? 1 : Math.max(0, previous - Math.max(0, Math.min(.1, dt)) * 2);
}
/** Fixed-count packet clustering: never implies an observed packet rate. */
export function threatPacketProgress(progress: number, profile: AttackProfile): number {
  return profile === 'dos' ? (Math.floor(progress * 3) + (progress * 3 % 1) ** 3) / 3 : progress;
}
