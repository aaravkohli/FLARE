/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BOUNDS } from './dynamics.ts';
import { TERRAIN_EXTENT } from './terrain.ts';
import { UAV_VISUAL, rotorDisplayAngle } from './uavVisual.ts';

test('restored quadrotor display mesh has its original footprint and flight clearance', () => {
  const span = 2 * (UAV_VISUAL.motorOffsetM + UAV_VISUAL.rotorRadiusM);
  assert.equal(span, 38);
  assert.deepEqual([UAV_VISUAL.bodyWidthM, UAV_VISUAL.bodyHeightM, UAV_VISUAL.bodyLengthM], [12, 5, 19]);
  assert.ok(UAV_VISUAL.bodyWidthM < 2 * UAV_VISUAL.motorOffsetM);
  assert.equal(UAV_VISUAL.selectionRingRadiusM, 18);
  assert.ok(BOUNDS.minAltitude - UAV_VISUAL.bodyHeightM / 2 > TERRAIN_EXTENT.maxWorldHeightM + 15);
});

test('display rotor phase is deterministic, counter-rotating and still in reduced motion', () => {
  const time = 1.25;
  assert.equal(rotorDisplayAngle(time, 0, false), -rotorDisplayAngle(time, 1, false));
  assert.equal(rotorDisplayAngle(time, 0, false), rotorDisplayAngle(time, 2, false));
  assert.equal(rotorDisplayAngle(time, 0, true), 0);
  assert.equal(rotorDisplayAngle(time, 1, true), 0);
});
