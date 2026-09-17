import type { Vector3 } from './dynamics.ts';
/** Compressed geocentric direction, not horizon visibility or radio coverage. */
export function satelliteWorldPosition(direction: Vector3): Vector3 {
  const norm = Math.hypot(direction.x, direction.y, direction.z);
  if (!Number.isFinite(norm) || norm === 0) throw new RangeError('Invalid orbital direction');
  return { x: 360 * direction.x / norm, y: 350 + 20 * direction.z / norm, z: -400 + 50 * direction.y / norm };
}
