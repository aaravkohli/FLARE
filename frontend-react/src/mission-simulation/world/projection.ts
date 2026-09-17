import { angleDifference } from './dynamics.ts';
import type { DroneMotion, Vector3 } from './dynamics.ts';

export const BASE = { x: 126, y: 352 };
interface Point { x: number; y: number }
function curvedPath(from: Point, to: Point, lift = 0) {
  return `M ${from.x} ${from.y} Q ${(from.x + to.x) / 2} ${(from.y + to.y) / 2 - lift} ${to.x} ${to.y}`;
}
export function pathGeometry(route: 'direct' | 'satellite' | 'mesh', drone: Point, satellite: Point, relay?: Point) {
  if (route === 'satellite') return `${curvedPath(drone, satellite, 18)} ${curvedPath(satellite, BASE, 38)}`;
  if (route === 'mesh' && relay) return `${curvedPath(drone, relay, 12)} ${curvedPath(relay, BASE, 22)}`;
  return curvedPath(drone, BASE, 48);
}

/** Orthographic oblique projection of a metre-based local simulation volume.
 * The satellite/base remain schematic network assets, not georeferenced objects.
 */
export function projectPosition(position: Vector3) {
  return { x: 465 + position.x * 0.88 + position.z * 0.24, y: 305 + position.z * 0.38 - position.y * 1.2 };
}
export function interpolateDrone(previous: DroneMotion, current: DroneMotion, alpha: number): DroneMotion {
  const t = Math.max(0, Math.min(1, alpha));
  return { ...current,
    position: {
      x: previous.position.x + (current.position.x - previous.position.x) * t,
      y: previous.position.y + (current.position.y - previous.position.y) * t,
      z: previous.position.z + (current.position.z - previous.position.z) * t,
    },
    heading: previous.heading + angleDifference(current.heading, previous.heading) * t,
    pitch: previous.pitch + (current.pitch - previous.pitch) * t,
    roll: previous.roll + (current.roll - previous.roll) * t,
  };
}
export function bodyTransform(drone: DroneMotion) {
  const projectedHeading = Math.atan2(Math.sin(drone.heading) * 0.38, Math.cos(drone.heading) * 0.88 + Math.sin(drone.heading) * 0.24);
  return `rotate(${projectedHeading * 180 / Math.PI}) skewX(${drone.roll * 28}) scale(1 ${0.85 - Math.abs(drone.pitch) * 0.5})`;
}
