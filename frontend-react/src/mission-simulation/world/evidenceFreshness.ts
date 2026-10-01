import type { MissionTelemetryPayload } from '../types.ts';

export type MissionTransportStatus = 'connecting' | 'connected' | 'disconnected';
export type MissionEvidenceKind = 'fresh' | 'stale' | 'disconnected' | 'unavailable' | 'awaiting' | 'unknown-age';

export interface MissionEvidenceStatus {
  kind: MissionEvidenceKind;
  label: string;
  ageSeconds: number | null;
  evidenceCurrent: boolean;
  lastSnapshot: boolean;
}

interface MissionEvidenceInput {
  payload: MissionTelemetryPayload | null;
  selectedDroneId: string;
  transportStatus: MissionTransportStatus;
  telemetryUnavailable?: string | null;
  receivedAtMs?: number | null;
  nowMs: number;
}

export const MISSION_FRESHNESS_LIMIT_S = 5;

/** A socket connection alone never proves that the selected drone has current evidence. */
export function missionEvidenceStatus(input: MissionEvidenceInput): MissionEvidenceStatus {
  const hasSnapshot = input.payload?.type === 'telemetry'
    && input.payload.metrics.drone_id === input.selectedDroneId;
  if (input.telemetryUnavailable) {
    return { kind: 'unavailable', label: 'Telemetry unavailable', ageSeconds: null,
      evidenceCurrent: false, lastSnapshot: Boolean(hasSnapshot) };
  }
  if (input.transportStatus !== 'connected') {
    return { kind: 'disconnected', label: input.transportStatus === 'connecting' ? 'Reconnecting' : 'Disconnected',
      ageSeconds: null, evidenceCurrent: false, lastSnapshot: Boolean(hasSnapshot) };
  }
  if (!hasSnapshot) {
    return { kind: 'awaiting', label: 'Awaiting selected-drone telemetry', ageSeconds: null,
      evidenceCurrent: false, lastSnapshot: false };
  }
  const serverAge = input.payload!.telemetry_age_s;
  if (serverAge == null || !Number.isFinite(serverAge) || serverAge < 0
    || input.receivedAtMs == null || !Number.isFinite(input.receivedAtMs)
    || !Number.isFinite(input.nowMs)) {
    return { kind: 'unknown-age', label: 'Telemetry age unreported', ageSeconds: null,
      evidenceCurrent: false, lastSnapshot: true };
  }
  const ageSeconds = serverAge + Math.max(0, input.nowMs - input.receivedAtMs) / 1000;
  if (ageSeconds > MISSION_FRESHNESS_LIMIT_S) {
    return { kind: 'stale', label: `Stale ${ageSeconds.toFixed(1)} s`, ageSeconds,
      evidenceCurrent: false, lastSnapshot: true };
  }
  return { kind: 'fresh', label: `Fresh ${ageSeconds.toFixed(1)} s`, ageSeconds,
    evidenceCurrent: true, lastSnapshot: false };
}
