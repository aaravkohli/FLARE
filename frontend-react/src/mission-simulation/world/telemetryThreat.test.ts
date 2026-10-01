/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { adaptMissionSimulationState } from '../adapter.ts';
import { telemetryThreatRows } from './telemetryThreat.ts';

test('safe inspected route score and high reported mission threat remain separate', () => {
  assert.deepEqual(telemetryThreatRows('Satellite', 0, 'HIGH'), [
    ['Satellite threat score', '0.00'],
    ['Reported mission threat', 'HIGH'],
  ]);
});

test('fallback state cannot turn an absent reported score or level into evidence', () => {
  const state = adaptMissionSimulationState(null, {
    droneId: 'drone_1', route: 'satellite', threatLevel: 'HIGH', reward: 0,
    step: 0, noSafeRoute: false, availablePaths: ['direct', 'satellite', 'mesh'],
  });
  const route = state.routes.find(item => item.id === 'satellite');
  assert.deepEqual(telemetryThreatRows('Satellite', route?.threatScore, state.reportedThreatLevel), [
    ['Satellite threat score', 'Unavailable'],
    ['Reported mission threat', 'Unavailable'],
  ]);
  assert.equal(telemetryThreatRows('Direct', Number.NaN, '')[0][1], 'Unavailable');
});
