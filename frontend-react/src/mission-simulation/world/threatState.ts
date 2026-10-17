import type { AttackProfile, MissionSimulationState } from '../types.ts';
export interface ThreatVisual {
  profile: AttackProfile; paths: string[]; drift: number | null; label: string;
}
export function threatVisual(state: MissionSimulationState, selectedId: string): ThreatVisual {
  const profile = state.droneId === selectedId ? state.activeAttack : 'none';
  const drift = profile === 'gps_spoofing' && Number.isFinite(state.gps?.drift_m) && state.gps!.drift_m > 0 ? state.gps!.drift_m : null;
  const detail: Record<AttackProfile, string> = {
    none: 'No active attack', spot: 'Spot interference · schematic region', sweep: 'Sweep interference · direction schematic',
    barrage: 'Barrage interference · schematic region', smart: 'Smart interference · reported affected links',
    reactive: 'Reactive interference · reported affected links', adaptive: 'Adaptive interference · reported affected links',
    fhss: 'Frequency-hopping mitigation · simulated', spoofing: 'Reported telemetry spoofing',
    gps_spoofing: drift === null ? 'GPS spoofing · reported drift unavailable' : `Reported drift ${drift.toFixed(1)} m · direction schematic`,
    replay: 'Repeated telemetry · flight position and age not established', dos: 'DoS · illustrative congestion, not measured traffic volume',
  };
  return { profile, paths: profile === 'none' ? [] : [...state.jammedPaths], drift, label: detail[profile] };
}
/** Only a previously active visual can decay. A new none state creates nothing. */
export function threatOpacity(previous: number, active: boolean, dt: number): number {
  return active ? 1 : Math.max(0, previous - Math.max(0, Math.min(.1, dt)) * 2);
}
/** Fixed-count packet clustering: never implies an observed packet rate. */
export function threatPacketProgress(progress: number, profile: AttackProfile): number {
  return profile === 'dos' ? (Math.floor(progress * 3) + (progress * 3 % 1) ** 3) / 3 : progress;
}
