import { GROUND_STATION, TERRAIN_EXTENT, terrainHeight } from './terrain.ts';

/** Illustrative scene dressing. Coordinates and species are not GIS observations. */
export interface EnvironmentProp {
  x: number;
  y: number;
  z: number;
  radiusM: number;
  rotation: number;
  tone: number;
  kind: 'scrub' | 'rock';
}

export const GROUND_SCALE_CUE = Object.freeze({ westX: -405, eastX: -355, z: 300, lengthM: 50 });

function random01(column: number, row: number, salt: number): number {
  let value = Math.imul(column + 12289, 374761393) ^ Math.imul(row + 8191, 668265263) ^ salt;
  value = Math.imul(value ^ value >>> 13, 1274126177);
  return ((value ^ value >>> 16) >>> 0) / 4294967296;
}

function nearOperationalSightline(x: number, z: number): boolean {
  const patrolCore = Math.abs(x) < 310 && Math.abs(z) < 225;
  const station = Math.hypot(x - GROUND_STATION.x, z - GROUND_STATION.z) < 100;
  const scaleCue = Math.hypot(x - (GROUND_SCALE_CUE.westX + GROUND_SCALE_CUE.eastX) / 2, z - GROUND_SCALE_CUE.z) < 75;
  return patrolCore || station || scaleCue;
}

/** Stable cell jitter and low density keep the fleet, route, station and labels clear. */
export function createEnvironmentProps(): readonly EnvironmentProp[] {
  const result: EnvironmentProp[] = [];
  for (let row = -13; row <= 13; row++) for (let column = -11; column <= 11; column++) {
    const x = column * 78 + (random01(column, row, 1) - .5) * 38;
    const z = row * 78 + (random01(column, row, 2) - .5) * 38;
    if (Math.abs(x) > TERRAIN_EXTENT.widthM / 2 - 230 ||
        Math.abs(z) > TERRAIN_EXTENT.depthM / 2 - 230 || nearOperationalSightline(x, z)) continue;
    const choice = random01(column, row, 3);
    const kind = choice < .20 ? 'scrub' : choice < .30 ? 'rock' : null;
    if (!kind) continue;
    const eastSlope = (terrainHeight(x + 3, z) - terrainHeight(x - 3, z)) / 6;
    const southSlope = (terrainHeight(x, z + 3) - terrainHeight(x, z - 3)) / 6;
    if (Math.hypot(eastSlope, southSlope) > .55) continue;
    result.push({
      x, y: terrainHeight(x, z), z, kind,
      radiusM: kind === 'scrub' ? 1.4 + random01(column, row, 4) * 1.4 : 1 + random01(column, row, 4) * 2,
      rotation: random01(column, row, 5) * Math.PI * 2,
      tone: random01(column, row, 6),
    });
  }
  return result;
}
