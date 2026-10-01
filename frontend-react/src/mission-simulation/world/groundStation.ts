import { GROUND_STATION, terrainHeight } from './terrain.ts';

/** Illustrative local-metre layout; the network endpoint stays at GROUND_STATION. */
export const GROUND_STATION_SITE = Object.freeze({
  shelter: Object.freeze({ x: -21, z: -7, width: 32, depth: 22, height: 10, slabDepth: 1.8 }),
  mast: Object.freeze({ x: 20, z: -13, width: 11, depth: 11, height: 30, slabDepth: 1.6 }),
  equipment: Object.freeze({ x: 0, z: -6, width: 10, depth: 9, height: 7, slabDepth: 1.6 }),
  servicePad: Object.freeze({ x: 21, z: 17, width: 30, depth: 24, slabDepth: 1.8 }),
});

export function stationGroundY(x: number, z: number): number {
  return terrainHeight(GROUND_STATION.x + x, GROUND_STATION.z + z) - GROUND_STATION.y;
}

/** A shallow, partly buried footing follows the highest terrain within its footprint. */
export function stationFootingTopY(x: number, z: number, width: number, depth: number): number {
  let highest = -Infinity;
  for (let row = 0; row <= 4; row++) for (let column = 0; column <= 4; column++) {
    highest = Math.max(highest, stationGroundY(x + width * (column / 4 - .5), z + depth * (row / 4 - .5)));
  }
  return highest + .22;
}

/** Scene geometry only: routes and threat marks visually meet the antenna head. */
const mastSite = GROUND_STATION_SITE.mast;
export const GROUND_STATION_VISUAL_ENDPOINT = Object.freeze({
  x: GROUND_STATION.x + mastSite.x,
  y: GROUND_STATION.y + stationFootingTopY(mastSite.x, mastSite.z, mastSite.width, mastSite.depth) + mastSite.height,
  z: GROUND_STATION.z + mastSite.z,
});
