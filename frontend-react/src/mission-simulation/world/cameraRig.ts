import type { Vector3 } from './dynamics.ts';
export const CAMERA_FOV = 44;
export type CameraMode = 'tactical' | 'overview' | 'follow';
export interface CameraRig { yaw: number; elevation: number; span: number; target: Vector3 }
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
export function cameraPreset(mode: CameraMode, selected?: Vector3): CameraRig {
  return { yaw: .28, elevation: (mode === 'overview' ? 64 : 38) * Math.PI / 180,
    span: mode === 'follow' && selected ? 650 : mode === 'overview' ? 1300 : 1150,
    target: mode === 'follow' && selected ? { ...selected } : { x: 0, y: 150, z: mode === 'overview' ? -100 : 0 } };
}
export function clampCamera(rig: CameraRig): CameraRig {
  return { yaw: clamp(rig.yaw, -.8, .8), elevation: clamp(rig.elevation, Math.PI / 6, Math.PI * .44),
    span: clamp(rig.span, 560, 1350), target: {
      x: clamp(rig.target.x, -360, 360), y: clamp(rig.target.y, 35, 160), z: clamp(rig.target.z, -240, 240),
    } };
}
export function dampCamera(current: CameraRig, desired: CameraRig, dt: number, reduced = false): CameraRig {
  const goal = clampCamera(desired);
  const alpha = reduced ? 1 : 1 - Math.exp(-5 * Math.max(0, Math.min(.1, dt)));
  const mix = (a: number, b: number) => a + (b - a) * alpha;
  return { yaw: mix(current.yaw, goal.yaw), elevation: mix(current.elevation, goal.elevation), span: mix(current.span, goal.span),
    target: { x: mix(current.target.x, goal.target.x), y: mix(current.target.y, goal.target.y), z: mix(current.target.z, goal.target.z) } };
}
/** Fixed camera distance is exclusively render space, never orbital metres. */
export function cameraEye(rig: CameraRig, aspect = 1.5): Vector3 {
  const distance = rig.span / (2 * Math.tan(CAMERA_FOV * Math.PI / 360) * Math.min(Math.max(aspect, .5), 1.5));
  const horizontal = distance * Math.cos(rig.elevation);
  return { x: rig.target.x + horizontal * Math.sin(rig.yaw), y: rig.target.y + distance * Math.sin(rig.elevation), z: rig.target.z + horizontal * Math.cos(rig.yaw) };
}
