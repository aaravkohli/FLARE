import type { MissionTelemetryPayload } from '../mission-simulation/types';
import type { SwarmSnapshot } from '../mission-simulation/swarm';

export const destinations = ['overview', 'mission', 'fleet', 'security', 'validation'] as const;
export type Destination = typeof destinations[number];
export function destination(hash: string): Destination {
  const value = hash.replace(/^#\/?/, '').toLowerCase();
  return destinations.includes(value as Destination) ? value as Destination : 'overview';
}
export const title = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
export const finite = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
export function metric(value: unknown, digits = 2, suffix = '') {
  const number = finite(value);
  return number === null ? '—' : `${number.toFixed(digits)}${suffix}`;
}
export function compare(a: unknown, b: unknown, descending = false): number {
  const missing = (v: unknown) => v == null || typeof v === 'number' && !Number.isFinite(v);
  if (missing(a) || missing(b)) return missing(a) === missing(b) ? 0 : missing(a) ? 1 : -1;
  const order = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
  return descending ? -order : order;
}
export function matches(query: string, ...values: unknown[]) {
  return values.some(value => String(value ?? '').toLowerCase().includes(query.trim().toLowerCase()));
}
export function fleetMeasurement(id: string, mission: SwarmSnapshot | null, payload?: MissionTelemetryPayload) {
  const current = !mission || payload?.event?.mission_context?.session_id === mission.session_id ? payload : undefined;
  const decision = current?.event?.decision;
  const installed = decision && 'installed_path' in decision ? decision.installed_path : current?.decision.sdn_applied === true ? current.decision.path_name : null;
  const route = mission ? mission.flows.find(f => f.source === id && f.destination === 'base' && f.traffic_class === 'telemetry')?.family ?? null : ['direct', 'satellite', 'mesh', 'hold'].includes(installed ?? '') ? installed : null;
  const path = current?.metrics.paths.find(p => p.path_id === route);
  return { route, pdr: finite(path?.pdr), latency: finite(path?.latency), source: (current?.event?.telemetry as {source?: string} | undefined)?.source ?? null };
}
export function updateLabel(row: { status?: string; action?: string }) {
  if (row.status === 'NO_UPDATE') return 'No update';
  return ({ ACCEPTED: 'Accepted', DOWN_WEIGHTED: 'Down-weighted', REJECTED: 'Rejected', PENDING: 'Pending' })[row.action ?? ''] ?? 'Unavailable';
}
export function signed(value: number) { return `${value >= 0 ? '+' : ''}${value}`; }
export type ActivityEntry = { id: string; timestamp: number; message: string; type?: string; severity?: string; source?: string; details?: unknown };
export type ChartDataPoint = { time: string; reward: number } & Record<`${'rssi' | 'sinr' | 'latency' | 'loss'}_${'direct' | 'satellite' | 'mesh'}`, number | null>;

/** Session resets discard retained federation evidence, while allowing a matching HTTP response to precede the first snapshot. */
export function federationForSession<T extends { session_id?: string; latest?: { mission_session?: string; [key: string]: unknown } }>(evidence: T | null, session: string): T | null {
  return evidence?.session_id === session || evidence?.latest?.mission_session === session ? evidence : null;
}
