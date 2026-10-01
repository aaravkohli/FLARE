import assert from 'node:assert/strict';
import test from 'node:test';
import { missionEvidenceStatus, MISSION_FRESHNESS_LIMIT_S } from './evidenceFreshness.ts';
import type { MissionTelemetryPayload } from '../types.ts';

const snapshot = (age: number | undefined, droneId = 'drone_1'): MissionTelemetryPayload => ({
  type: 'telemetry', timestamp: 1,
  decision: { path_name: 'direct', threat_level: 'LOW', reward: 0, step: 1 },
  metrics: { drone_id: droneId, timestamp: 1, source: 'synthetic', paths: [] },
  telemetry_age_s: age,
});
const input = (payload: MissionTelemetryPayload | null) => ({
  payload, selectedDroneId: 'drone_1', transportStatus: 'connected' as const,
  receivedAtMs: 1_000, nowMs: 1_000,
});

test('freshness advances the server-reported age with local elapsed time', () => {
  const payload = snapshot(1.2);
  const initial = missionEvidenceStatus(input(payload));
  assert.equal(initial.kind, 'fresh');
  assert.equal(initial.ageSeconds, 1.2);
  assert.equal(initial.evidenceCurrent, true);
  const stale = missionEvidenceStatus({ ...input(payload), nowMs: 1_000 + MISSION_FRESHNESS_LIMIT_S * 1_000 });
  assert.equal(stale.kind, 'stale');
  assert.equal(stale.ageSeconds, 6.2);
  assert.equal(stale.evidenceCurrent, false);
});

test('a retained snapshot is explicitly last-known during disconnect or telemetry failure', () => {
  const payload = snapshot(0);
  const disconnected = missionEvidenceStatus({ ...input(payload), transportStatus: 'disconnected' });
  assert.equal(disconnected.kind, 'disconnected');
  assert.equal(disconnected.lastSnapshot, true);
  assert.equal(disconnected.evidenceCurrent, false);
  const unavailable = missionEvidenceStatus({ ...input(payload), telemetryUnavailable: 'sensor failed' });
  assert.equal(unavailable.kind, 'unavailable');
  assert.equal(unavailable.lastSnapshot, true);
  assert.equal(unavailable.evidenceCurrent, false);
});

test('missing selected evidence and unknown or invalid age never enable packet flow', () => {
  assert.equal(missionEvidenceStatus(input(null)).kind, 'awaiting');
  assert.equal(missionEvidenceStatus(input(snapshot(0, 'drone_2'))).kind, 'awaiting');
  for (const age of [undefined, NaN, -1]) {
    const evidence = missionEvidenceStatus(input(snapshot(age)));
    assert.equal(evidence.kind, 'unknown-age');
    assert.equal(evidence.evidenceCurrent, false);
  }
  assert.equal(missionEvidenceStatus({ ...input(snapshot(0)), receivedAtMs: null }).kind, 'unknown-age');
});
