/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BOUNDS } from './dynamics.ts';
import { GROUND_STATION, terrainHeight } from './terrain.ts';
import { GROUND_STATION_SITE, GROUND_STATION_VISUAL_ENDPOINT, stationFootingTopY, stationGroundY } from './groundStation.ts';

test('illustrative GCS footprints stay grounded, local and below the flight volume', () => {
  assert.equal(GROUND_STATION.y, terrainHeight(GROUND_STATION.x, GROUND_STATION.z) + 5);
  assert.equal(stationGroundY(0, 0), -5);
  assert.deepEqual(GROUND_STATION_VISUAL_ENDPOINT, {
    x: GROUND_STATION.x + GROUND_STATION_SITE.mast.x,
    y: GROUND_STATION.y + stationFootingTopY(
      GROUND_STATION_SITE.mast.x, GROUND_STATION_SITE.mast.z,
      GROUND_STATION_SITE.mast.width, GROUND_STATION_SITE.mast.depth,
    ) + GROUND_STATION_SITE.mast.height,
    z: GROUND_STATION.z + GROUND_STATION_SITE.mast.z,
  });
  const footprints = Object.values(GROUND_STATION_SITE);
  for (const site of footprints) {
    const top = stationFootingTopY(site.x, site.z, site.width, site.depth);
    for (let row = 0; row <= 8; row++) for (let column = 0; column <= 8; column++) {
      const ground = stationGroundY(
        site.x + site.width * (column / 8 - .5),
        site.z + site.depth * (row / 8 - .5),
      );
      assert.ok(ground <= top + .05);
      assert.ok(ground >= top - site.slabDepth, 'footing must not float above sampled ground');
    }
    assert.ok(Math.abs(GROUND_STATION.x + site.x) + site.width / 2 < BOUNDS.x);
    assert.ok(Math.abs(GROUND_STATION.z + site.z) + site.depth / 2 < BOUNDS.z);
    if ('height' in site) assert.ok(GROUND_STATION.y + top + site.height + 4 < BOUNDS.minAltitude);
  }
});

test('shelter, mast and service pad leave distinct readable footprints', () => {
  const { shelter, mast, servicePad } = GROUND_STATION_SITE;
  assert.ok(shelter.x + shelter.width / 2 < mast.x - mast.width / 2);
  assert.ok(shelter.z + shelter.depth / 2 < servicePad.z - servicePad.depth / 2);
  assert.ok(mast.z + mast.depth / 2 < servicePad.z - servicePad.depth / 2);
});
