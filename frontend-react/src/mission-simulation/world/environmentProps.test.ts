/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createEnvironmentProps, GROUND_SCALE_CUE } from './environmentProps.ts';
import { GROUND_STATION, TERRAIN_DISCLOSURE, TERRAIN_EXTENT, terrainHeight } from './terrain.ts';

test('illustrative props are deterministic, bounded, grounded and clear of the operational core', () => {
  const props = createEnvironmentProps();
  assert.deepEqual(props, createEnvironmentProps());
  assert.ok(props.length >= 100 && props.length <= 200, `bounded density ${props.length}`);
  assert.ok(props.some(prop => prop.kind === 'scrub'));
  assert.ok(props.some(prop => prop.kind === 'rock'));
  for (const prop of props) {
    assert.ok(Number.isFinite(prop.x) && Number.isFinite(prop.y) && Number.isFinite(prop.z));
    assert.ok(Math.abs(prop.x) < TERRAIN_EXTENT.widthM / 2 - 230);
    assert.ok(Math.abs(prop.z) < TERRAIN_EXTENT.depthM / 2 - 230);
    assert.equal(prop.y, terrainHeight(prop.x, prop.z));
    assert.ok(!(Math.abs(prop.x) < 310 && Math.abs(prop.z) < 225));
    assert.ok(Math.hypot(prop.x - GROUND_STATION.x, prop.z - GROUND_STATION.z) >= 100);
    assert.ok(Math.hypot(prop.x - (GROUND_SCALE_CUE.westX + GROUND_SCALE_CUE.eastX)/2, prop.z - GROUND_SCALE_CUE.z) >= 75);
    assert.ok(prop.radiusM >= 1 && prop.radiusM <= 3);
  }
  assert.match(TERRAIN_DISCLOSURE, /illustrative.*props/);
});

test('local ground scale cue represents exactly 50 scene metres away from the fleet route', () => {
  assert.equal(GROUND_SCALE_CUE.eastX - GROUND_SCALE_CUE.westX, GROUND_SCALE_CUE.lengthM);
  assert.equal(GROUND_SCALE_CUE.lengthM, 50);
  assert.ok(GROUND_SCALE_CUE.westX > -TERRAIN_EXTENT.widthM/2);
  assert.ok(Math.abs(GROUND_SCALE_CUE.z) < TERRAIN_EXTENT.depthM/2);
  assert.ok(Math.hypot(GROUND_SCALE_CUE.westX - GROUND_STATION.x, GROUND_SCALE_CUE.z - GROUND_STATION.z) > 130);
});
