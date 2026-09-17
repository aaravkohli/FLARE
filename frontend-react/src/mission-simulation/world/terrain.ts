/** Static illustrative relief, not surveyed terrain. Always below the UAV floor. */
export function terrainHeight(x: number, z: number): number {
  return 13 * Math.exp(-((x + 220) ** 2 / 65000 + (z + 140) ** 2 / 19000))
    + 8 * Math.exp(-((x - 260) ** 2 / 24000 + (z - 80) ** 2 / 43000)) - 3;
}
export const GROUND_STATION = Object.freeze({ x: -280, y: terrainHeight(-280, 170) + 5, z: 170 });
