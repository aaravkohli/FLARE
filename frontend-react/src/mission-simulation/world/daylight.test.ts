/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DAYLIGHT, groundShadowPosition } from './daylight.ts';
import { terrainHeight } from './terrain.ts';

test('bounded daylight shadow frustum covers the local patrol and uses one stable sun direction', () => {
  assert.ok(DAYLIGHT.shadowMapSize <= 1024);
  assert.ok(DAYLIGHT.shadowHalfSpanM > 360);
  assert.ok(DAYLIGHT.fogNearM > 1300 && DAYLIGHT.fogFarM < 4500);
  const drone = Object.freeze({x:0,y:100,z:0});
  const flat = groundShadowPosition(drone, () => 0);
  assert.ok(Math.abs(flat.x - 100 * -DAYLIGHT.sun.x / DAYLIGHT.sun.y) < 1e-9);
  assert.ok(Math.abs(flat.z - 100 * -DAYLIGHT.sun.z / DAYLIGHT.sun.y) < 1e-9);
  assert.equal(flat.y,.6);
  assert.deepEqual(drone,{x:0,y:100,z:0});
});

test('terrain-projected drone indicator remains grounded and aligned with the daylight ray', () => {
  for (const position of [{x:0,y:70,z:0},{x:-210,y:110,z:120},{x:220,y:80,z:-150}]) {
    const result = groundShadowPosition(position,terrainHeight);
    assert.ok(Object.values(result).every(Number.isFinite));
    assert.ok(Math.abs(result.y-terrainHeight(result.x,result.z)-.6)<1e-8);
    const altitude=position.y-(result.y-.6);
    assert.ok(Math.abs(result.x-position.x+altitude*DAYLIGHT.sun.x/DAYLIGHT.sun.y)<1e-4);
    assert.ok(Math.abs(result.z-position.z+altitude*DAYLIGHT.sun.z/DAYLIGHT.sun.y)<1e-4);
  }
});
