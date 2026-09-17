import type { Vector3 } from './dynamics.ts';
export interface RouteCurve { points: readonly Vector3[] }
/** A schematic lifted curve. Its endpoints are current visual positions, not RF evidence. */
export function routeCurve(start: Vector3, end: Vector3, satellite?: Vector3): RouteCurve {
  return { points: satellite ? [start, satellite, end] : [start, end] };
}
export function curvePoint(curve: RouteCurve, progress: number, target: Vector3 = {x:0,y:0,z:0}): Vector3 {
  const t = Math.max(0, Math.min(1, progress)) * (curve.points.length - 1);
  const leg = Math.min(curve.points.length - 2, Math.floor(t));
  const local = t - leg, a = curve.points[leg], b = curve.points[leg + 1];
  target.x = a.x + (b.x-a.x)*local;
  target.y = a.y+(b.y-a.y)*local + Math.min(90, Math.hypot(b.x-a.x,b.z-a.z)*.18)*4*local*(1-local);
  target.z = a.z+(b.z-a.z)*local;
  return target;
}
