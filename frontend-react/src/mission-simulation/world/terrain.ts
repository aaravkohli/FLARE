import { HEIGHTMAP_BASE64, TERRAIN_GRID } from '../assets/terrain/heightmap.ts';
import { BOUNDS } from './dynamics.ts';

/** Local display metres, not the UAV's geographic position or an RF input. */
export interface TerrainGrid {
  columns: number;
  rows: number;
  worldWidthM: number;
  worldDepthM: number;
  elevationBaseM: number;
  elevationStepM: number;
  minimumElevationM: number;
  maximumElevationM: number;
  maxWorldHeightM: number;
  edgeBlendM: number;
  encodedFnv1a32: string;
}

export interface TerrainSurface {
  provenance: 'usgs-3dep' | 'representative';
  height(x: number, z: number): number;
  elevation(x: number, z: number): number | null;
}

export const TERRAIN_EXTENT = Object.freeze({
  widthM: TERRAIN_GRID.worldWidthM,
  depthM: TERRAIN_GRID.worldDepthM,
  columns: TERRAIN_GRID.columns,
  rows: TERRAIN_GRID.rows,
  maxWorldHeightM: TERRAIN_GRID.maxWorldHeightM,
});

/** Backup appearance for corrupt/missing encoded data and the surrounding horizon. */
export function representativeTerrainHeight(x: number, z: number): number {
  return 13 * Math.exp(-((x + 220) ** 2 / 65000 + (z + 140) ** 2 / 19000))
    + 8 * Math.exp(-((x - 260) ** 2 / 24000 + (z - 80) ** 2 / 43000)) - 3;
}

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const smoothstep = (value: number) => value * value * (3 - 2 * value);

function decodeSamples(encoded: string, grid: TerrainGrid): Uint16Array {
  if (!Number.isInteger(grid.columns) || !Number.isInteger(grid.rows) || grid.columns < 2 || grid.rows < 2 ||
      !Number.isFinite(grid.worldWidthM) || grid.worldWidthM <= 0 ||
      !Number.isFinite(grid.worldDepthM) || grid.worldDepthM <= 0 ||
      !Number.isFinite(grid.elevationStepM) || grid.elevationStepM <= 0 ||
      !Number.isFinite(grid.elevationBaseM) || !Number.isFinite(grid.maximumElevationM) ||
      !Number.isFinite(grid.maxWorldHeightM) || grid.maxWorldHeightM >= BOUNDS.minAltitude - 10 ||
      !Number.isFinite(grid.edgeBlendM) || grid.edgeBlendM < 0 ||
      grid.edgeBlendM >= Math.min(grid.worldWidthM, grid.worldDepthM) / 2) {
    throw new Error('Invalid mission terrain metadata');
  }
  const binary = atob(encoded);
  if (binary.length !== grid.columns * grid.rows * 2) throw new Error('Invalid mission terrain sample count');
  const samples = new Uint16Array(grid.columns * grid.rows);
  let checksum = 2166136261;
  let minimum = Infinity, maximum = -Infinity;
  for (let i = 0; i < samples.length; i++) {
    const lo = binary.charCodeAt(i * 2), hi = binary.charCodeAt(i * 2 + 1);
    checksum = Math.imul(checksum ^ lo, 16777619) >>> 0;
    checksum = Math.imul(checksum ^ hi, 16777619) >>> 0;
    const sample = lo | hi << 8;
    samples[i] = sample;
    minimum = Math.min(minimum, sample);
    maximum = Math.max(maximum, sample);
  }
  if (checksum.toString(16).padStart(8, '0') !== grid.encodedFnv1a32 ||
      Math.abs(grid.elevationBaseM + minimum * grid.elevationStepM - grid.minimumElevationM) > grid.elevationStepM ||
      Math.abs(grid.elevationBaseM + maximum * grid.elevationStepM - grid.maximumElevationM) > grid.elevationStepM) {
    throw new Error('Mission terrain checksum or range mismatch');
  }
  return samples;
}

/** Pure decoder, independently testable with damaged data for fallback behavior. */
export function createTerrainSurface(encoded: string, grid: TerrainGrid): TerrainSurface {
  let samples: Uint16Array;
  try { samples = decodeSamples(encoded, grid); }
  catch {
    return { provenance: 'representative', height: representativeTerrainHeight, elevation: () => null };
  }
  const halfWidth = grid.worldWidthM / 2, halfDepth = grid.worldDepthM / 2;
  function elevation(x: number, z: number): number | null {
    if (!Number.isFinite(x) || !Number.isFinite(z) || Math.abs(x) > halfWidth || Math.abs(z) > halfDepth) return null;
    const column = (x + halfWidth) / grid.worldWidthM * (grid.columns - 1);
    const row = (z + halfDepth) / grid.worldDepthM * (grid.rows - 1);
    const left = Math.min(grid.columns - 2, Math.floor(column));
    const north = Math.min(grid.rows - 2, Math.floor(row));
    const eastFraction = column - left, southFraction = row - north;
    const offset = north * grid.columns + left;
    const top = samples[offset] * (1 - eastFraction) + samples[offset + 1] * eastFraction;
    const bottom = samples[offset + grid.columns] * (1 - eastFraction) + samples[offset + grid.columns + 1] * eastFraction;
    return grid.elevationBaseM + (top * (1 - southFraction) + bottom * southFraction) * grid.elevationStepM;
  }
  function height(x: number, z: number): number {
    const sourceElevation = elevation(x, z);
    const representative = representativeTerrainHeight(x, z);
    if (sourceElevation === null) return representative;
    const distanceToEdge = Math.min(halfWidth - Math.abs(x), halfDepth - Math.abs(z));
    const weight = smoothstep(clamp(distanceToEdge / Math.max(grid.edgeBlendM, 1), 0, 1));
    const localHeight = sourceElevation - grid.maximumElevationM + grid.maxWorldHeightM;
    return representative + (localHeight - representative) * weight;
  }
  return { provenance: 'usgs-3dep', height, elevation };
}

const missionTerrain = createTerrainSurface(HEIGHTMAP_BASE64, TERRAIN_GRID);
export const TERRAIN_PROVENANCE = missionTerrain.provenance;
export const TERRAIN_DISCLOSURE = missionTerrain.provenance === 'usgs-3dep'
  ? 'USGS 3DEP elevation core · representative outer terrain · illustrative land cover and props, roads, launch pads and facilities · visual only, no RF or flight effect · FLARE local-metre movement, not surveyed UAV positions'
  : 'Representative mission terrain and illustrative props, roads, launch pads and facilities · visual only, no RF or flight effect · FLARE local-metre movement';
export const terrainElevation = missionTerrain.elevation;
export const terrainHeight = missionTerrain.height;
export const GROUND_STATION = Object.freeze({ x: -280, y: terrainHeight(-280, 170) + 5, z: 170 });
