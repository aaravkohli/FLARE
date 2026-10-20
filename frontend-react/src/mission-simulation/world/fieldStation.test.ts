import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createSiteVegetation, drapedRectangle, drapedRing, drapedStrip, FIELD_STATION, siteFooting, siteVegetationAllowed, woodlandFloor, WOODLANDS } from './fieldStationLayout.ts';
import { createFieldStation } from './fieldStationScene.ts';
import { GROUND_STATION, terrainHeight, TERRAIN_DISCLOSURE, TERRAIN_EXTENT } from './terrain.ts';
import { GROUND_STATION_VISUAL_ENDPOINT } from './groundStation.ts';
import { BOUNDS } from './dynamics.ts';

test('site roads and pads follow unmodified relief with upward faces and finite geometry', () => {
  const surfaces = [drapedStrip(FIELD_STATION.approach.points, 8), drapedStrip(FIELD_STATION.loop.points, 6), ...FIELD_STATION.pads.map(p => drapedRing(p, 0, 16, .12))];
  for (const s of surfaces) {
    for (let i = 0; i < s.positions.length; i += 3) {
      const [x, y, z] = s.positions.slice(i, i + 3);
      assert.ok(Number.isFinite(x + y + z)); assert.ok(Math.abs(y - terrainHeight(x, z) - .12) < 1e-6);
      // The rendered mesh triangulates the DEM; bilinear height sampling alone
      // would miss a road clipping into the diagonal of a source grid cell.
      const t = TERRAIN_EXTENT, u = (x / t.widthM + .5) * (t.columns - 1), v = (z / t.depthM + .5) * (t.rows - 1);
      const column = Math.floor(u), row = Math.floor(v), fx = u - column, fz = v - row;
      const h = (c: number, r: number) => terrainHeight((c / (t.columns - 1) - .5) * t.widthM, (r / (t.rows - 1) - .5) * t.depthM);
      const meshY = fx + fz <= 1 ? h(column, row) * (1 - fx - fz) + h(column + 1, row) * fx + h(column, row + 1) * fz
        : h(column + 1, row + 1) * (fx + fz - 1) + h(column, row + 1) * (1 - fx) + h(column + 1, row) * (1 - fz);
      assert.ok(y > meshY && y - meshY < .4, 'ground dressing clears the rendered surface without floating');
    }
    assert.ok(s.indices.every(i => i >= 0 && i < s.positions.length / 3));
    for (let i = 0; i < s.indices.length; i += 3) {
      const [a, b, c] = s.indices.slice(i, i + 3).map(index => new THREE.Vector3().fromArray(s.positions, index * 3));
      assert.ok(b.sub(a).cross(c.sub(a)).y >= -1e-9, 'visible from above');
    }
  }
  const loop = drapedStrip(FIELD_STATION.loop.points, 6);
  assert.deepEqual(loop.positions.slice(0, 6), loop.positions.slice(-6), 'closed-loop edges meet without a corner gap');
  assert.equal(FIELD_STATION.buildings.length, 8); assert.equal(FIELD_STATION.pads.length, 3);
});

test('shallow equipment buildings have grounded footings and remain below flight clearance', () => {
  for (const b of FIELD_STATION.buildings) {
    const footing = siteFooting(b);
    assert.ok(footing.top + b.height + 2 < BOUNDS.minAltitude);
    const apron = FIELD_STATION.lots.apron;
    assert.ok(Math.abs(b.x - apron.x) > (b.width + apron.width) / 2 + 10 || Math.abs(b.z - apron.z) > (b.depth + apron.depth) / 2 + 10, 'architecture leaves the central apron open');
    for (let row = 0; row <= 8; row++) for (let column = 0; column <= 8; column++) {
      const height = terrainHeight(b.x + b.width * (column / 8 - .5), b.z + b.depth * (row / 8 - .5));
      assert.ok(footing.bottom < height && footing.top > height);
    }
  }
  assert.equal(GROUND_STATION.x, -280); assert.equal(GROUND_STATION.z, 170);
  assert.equal(GROUND_STATION_VISUAL_ENDPOINT.x, -260); assert.equal(GROUND_STATION_VISUAL_ENDPOINT.z, 157);
  assert.match(TERRAIN_DISCLOSURE, /illustrative.*roads.*facilities/);
  assert.match(TERRAIN_DISCLOSURE, /no RF or flight effect/);
});

test('clustered vegetation is deterministic, bounded and clear of roads and operational sightlines', () => {
  const props = createSiteVegetation(); assert.deepEqual(props, createSiteVegetation());
  assert.ok(props.length > 650 && props.length <= 950, `bounded density ${props.length}`);
  assert.ok(props.some(p => p.kind === 'tree')); assert.ok(props.some(p => !p.balanced));
  for (const p of props) {
    assert.ok(siteVegetationAllowed(p.x, p.z)); assert.equal(p.y, terrainHeight(p.x, p.z));
    assert.ok(p.radiusM >= 1 && p.radiusM <= 6.1); assert.ok(p.heightM > 0 && p.heightM <= 19);
    assert.ok(p.y + p.heightM < BOUNDS.minAltitude);
  }
  assert.equal(siteVegetationAllowed(0, 0), false);
  assert.equal(siteVegetationAllowed(...FIELD_STATION.pads[0]), false);
  assert.equal(siteVegetationAllowed(...FIELD_STATION.approach.points[0]), false);
});

test('land use and woodland masses occupy the visible range without changing terrain heights', () => {
  for (const lot of Object.values(FIELD_STATION.lots)) {
    const surface = drapedRectangle(lot);
    assert.ok(lot.width >= 100 && lot.depth >= 40, 'ground landmarks remain legible at mission zoom');
    for (let i = 0; i < surface.positions.length; i += 3) {
      const [x, y, z] = surface.positions.slice(i, i + 3); assert.ok(Math.abs(y - terrainHeight(x, z) - .28) < 1e-6);
    }
  }
  for (const [index, wood] of WOODLANDS.entries()) {
    const floor = woodlandFloor(wood, index + 1); assert.deepEqual(floor, woodlandFloor(wood, index + 1));
    assert.equal(floor.alpha.length, floor.positions.length / 3);
    assert.ok(floor.alpha.every(a => a >= 0 && a <= 1)); assert.ok(floor.alpha.some(a => a === 0));
    assert.ok(floor.alpha.some(a => a > .6));
  }
  const trees = createSiteVegetation().filter(p => p.kind === 'tree');
  assert.ok(trees.length >= 450); assert.ok(trees.some(p => p.broadleaf));
  assert.ok(trees.every(p => p.heightM > 9 && p.y + p.heightM <= BOUNDS.minAltitude - 8));
});

test('quality and visibility changes preserve scenery positions and release every owned resource once', () => {
  const site = createFieldStation(), parent = new THREE.Scene(); parent.add(site.group);
  const meshes: THREE.Mesh[] = []; site.group.traverse(o => { if (o instanceof THREE.Mesh) meshes.push(o); });
  const shapes = new Set(meshes.map(m => m.geometry));
  const materials = new Set(meshes.flatMap(m => Array.isArray(m.material) ? m.material : [m.material]));
  const positions = meshes.map(m => m.position.clone());
  const instances = meshes.filter((m): m is THREE.InstancedMesh => m instanceof THREE.InstancedMesh);
  const buffers = instances.map(m => m.instanceMatrix.array.slice());
  site.setQuality('high'); const highVisible = meshes.filter(m => m.visible).length;
  site.setQuality('balanced'); assert.ok(meshes.filter(m => m.visible).length < highVisible);
  site.setVisible(false); assert.equal(site.group.visible, false); site.setVisible(true);
  site.setQuality('high'); assert.equal(meshes.filter(m => m.visible).length, highVisible);
  assert.deepEqual(meshes.map(m => m.position), positions);
  assert.deepEqual(instances.map(m => m.instanceMatrix.array), buffers);
  assert.ok(meshes.every(m => !m.userData.asset), 'scenery never becomes a command or inspection target');
  const releases = new Map<object, number>();
  shapes.forEach(resource => resource.addEventListener('dispose', () => releases.set(resource, (releases.get(resource) ?? 0) + 1)));
  materials.forEach(resource => resource.addEventListener('dispose', () => releases.set(resource, (releases.get(resource) ?? 0) + 1)));
  instances.forEach(resource => resource.addEventListener('dispose', () => releases.set(resource, (releases.get(resource) ?? 0) + 1)));
  site.dispose(); site.dispose();
  assert.equal(site.group.parent, null); assert.equal(site.group.children.length, 0);
  assert.equal(releases.size, shapes.size + materials.size + instances.length);
  assert.ok([...releases.values()].every(count => count === 1));
});
