/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { HEIGHTMAP_BASE64, TERRAIN_GRID } from '../assets/terrain/heightmap.ts';
import {
  createTerrainSurface, GROUND_STATION, representativeTerrainHeight,
  terrainElevation, terrainHeight, TERRAIN_DISCLOSURE, TERRAIN_EXTENT, TERRAIN_PROVENANCE,
} from './terrain.ts';
import { BOUNDS } from './dynamics.ts';

const assetUrl = (name: string) => new URL(`../assets/terrain/${name}`, import.meta.url);

test('committed DEM bytes and generated heightmap match the explicit source manifest', () => {
  const manifest = JSON.parse(readFileSync(assetUrl('terrain-manifest.json'), 'utf8'));
  const digest = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
  assert.equal(digest(readFileSync(assetUrl('source-dem.tif'))), manifest.sourceSha256);
  assert.equal(digest(Buffer.from(HEIGHTMAP_BASE64, 'base64')), manifest.heightmapSha256);
  assert.deepEqual(TERRAIN_GRID, manifest.grid);
  assert.equal(manifest.crs, 'EPSG:4326');
  assert.deepEqual(manifest.requestBoundsWgs84, {west:-105.232,south:40.008,east:-105.204,north:40.032});
  assert.deepEqual(manifest.deliveredRasterBoundsWgs84, {west:-105.232,south:40.006,east:-105.204,north:40.034});
  assert.match(manifest.surfaceMaps, /not orthoimagery/);
});

test('source elevation samples map to local east and south without altering the 1:1 relief scale', () => {
  assert.equal(TERRAIN_PROVENANCE, 'usgs-3dep');
  assert.match(TERRAIN_DISCLOSURE, /local-metre movement/);
  const surface = createTerrainSurface(HEIGHTMAP_BASE64, TERRAIN_GRID);
  assert.equal(surface.provenance, 'usgs-3dep');
  assert.ok(Math.abs(surface.elevation(0,0)! - 1586.82) < .02);
  const halfWidth = TERRAIN_EXTENT.widthM/2, halfDepth = TERRAIN_EXTENT.depthM/2;
  const cornerBytes = Buffer.from(HEIGHTMAP_BASE64, 'base64');
  const northwest = TERRAIN_GRID.elevationBaseM + cornerBytes.readUInt16LE(0)*TERRAIN_GRID.elevationStepM;
  const southeast = TERRAIN_GRID.elevationBaseM + cornerBytes.readUInt16LE(cornerBytes.length-2)*TERRAIN_GRID.elevationStepM;
  assert.ok(Math.abs(surface.elevation(-halfWidth,-halfDepth)!-northwest)<1e-9);
  assert.ok(Math.abs(surface.elevation(halfWidth,halfDepth)!-southeast)<1e-9);
  assert.equal(surface.elevation(halfWidth+1,0),null);
  assert.ok(Math.abs(terrainHeight(0,0)-(terrainElevation(0,0)!-TERRAIN_GRID.maximumElevationM+17))<1e-9);
});

test('the rendered patch, edge blend and ground station preserve flight clearance', () => {
  let maximum = -Infinity;
  for (let x=-500;x<=500;x+=10) for(let z=-400;z<=400;z+=10){
    const height = terrainHeight(x,z);
    assert.ok(Number.isFinite(height));
    maximum = Math.max(maximum,height);
  }
  assert.ok(maximum <= TERRAIN_EXTENT.maxWorldHeightM);
  assert.ok(BOUNDS.minAltitude - TERRAIN_EXTENT.maxWorldHeightM >= 18);
  assert.equal(GROUND_STATION.y,terrainHeight(GROUND_STATION.x,GROUND_STATION.z)+5);
  const edge = TERRAIN_EXTENT.widthM/2;
  assert.ok(Math.abs(terrainHeight(edge-.01,0)-terrainHeight(edge+.01,0))<.01);
  assert.equal(terrainHeight(edge+100,0),representativeTerrainHeight(edge+100,0));
});

test('damaged or incompatible heightmaps render the representative fallback', () => {
  for(const encoded of ['',HEIGHTMAP_BASE64.slice(0,-12), 'not valid base64']){
    const fallback=createTerrainSurface(encoded,TERRAIN_GRID);
    assert.equal(fallback.provenance,'representative');
    assert.equal(fallback.height(0,0),representativeTerrainHeight(0,0));
    assert.equal(fallback.elevation(0,0),null);
  }
  const altered=createTerrainSurface(HEIGHTMAP_BASE64,{...TERRAIN_GRID,maxWorldHeightM:35});
  assert.equal(altered.provenance,'representative');
});
