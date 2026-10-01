import type { Vector3 } from './dynamics.ts';

/** Visual daylight only; this does not affect RF line of sight or UAV motion. */
export const DAYLIGHT = Object.freeze({
  sun: Object.freeze({ x: -420, y: 800, z: 360 }),
  shadowMapSize: 1024,
  shadowHalfSpanM: 750,
  shadowFarM: 1900,
  fogNearM: 1600,
  fogFarM: 4300,
});

/** Approximate the sun ray's intersection with the visual terrain heightfield. */
export function groundShadowPosition(position: Vector3, heightAt: (x: number, z: number) => number): Vector3 {
  let x = position.x, z = position.z;
  for (let i = 0; i < 5; i++) {
    const altitude = Math.max(0, position.y - heightAt(x, z));
    x = position.x - altitude * DAYLIGHT.sun.x / DAYLIGHT.sun.y;
    z = position.z - altitude * DAYLIGHT.sun.z / DAYLIGHT.sun.y;
  }
  return { x, y: heightAt(x, z) + .6, z };
}
