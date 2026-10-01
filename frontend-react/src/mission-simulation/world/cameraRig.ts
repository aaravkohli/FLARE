import { angleDifference } from './dynamics.ts';
import type { DroneMotion, Vector3 } from './dynamics.ts';
import { GROUND_STATION_VISUAL_ENDPOINT } from './groundStation.ts';
import { terrainHeight } from './terrain.ts';

export const CAMERA_FOV = 44;
export type CameraMode = 'overview' | 'tactical' | 'follow' | 'network';
export type NetworkFrame = 'direct' | 'mesh' | 'satellite' | 'unknown';
export type CameraSubject = Pick<DroneMotion, 'position' | 'heading'>;
export interface CameraRig { yaw: number; elevation: number; span: number; target: Vector3 }

const radians = (degrees: number) => degrees * Math.PI / 180;
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const midpoint = (a: Vector3, b: Vector3): Vector3 => ({
  x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2,
});
const overview = (): CameraRig => ({
  yaw: .28, elevation: radians(35), span: 1350, target: { x: 50, y: 190, z: 30 },
});

/** Camera coordinates are local visual metres; the orbit and flight state are unchanged. */
export function cameraPreset(mode: CameraMode, selected?: CameraSubject, networkFrame: NetworkFrame = 'unknown'): CameraRig {
  if (mode === 'overview') return overview();
  if (mode === 'follow') {
    if (!selected) return overview();
    const { position, heading } = selected;
    const forwardX = Math.cos(heading), forwardZ = Math.sin(heading);
    const visualClearance = position.y - terrainHeight(position.x, position.z);
    return {
      yaw: Math.atan2(-forwardX, -forwardZ), elevation: radians(35),
      span: clamp(2.6 * visualClearance, 240, 400),
      target: { x: position.x + 30 * forwardX, y: position.y - 15, z: position.z + 30 * forwardZ },
    };
  }
  if (mode === 'tactical') {
    const position = selected?.position ?? { x: 0, y: 80, z: 0 };
    return { yaw: .28, elevation: radians(38), span: 900,
      target: midpoint(position, GROUND_STATION_VISUAL_ENDPOINT) };
  }
  if (networkFrame === 'direct' && selected) {
    return { yaw: 0, elevation: radians(50), span: 900,
      target: midpoint(selected.position, GROUND_STATION_VISUAL_ENDPOINT) };
  }
  if (networkFrame === 'mesh') {
    // Peer lines show a neighborhood; no particular forwarding relay is known.
    return { yaw: 0, elevation: radians(50), span: 1100, target: { x: 0, y: 70, z: 0 } };
  }
  // The compressed orbital display is visible even when installation is unknown.
  return { yaw: 0, elevation: radians(38), span: 1350, target: { x: 50, y: 190, z: 30 } };
}

export function clampCamera(rig: CameraRig): CameraRig {
  return {
    yaw: Math.atan2(Math.sin(rig.yaw), Math.cos(rig.yaw)),
    elevation: clamp(rig.elevation, radians(25), Math.PI * .44),
    span: clamp(rig.span, 160, 1350),
    target: {
      x: clamp(rig.target.x, -420, 420),
      y: clamp(rig.target.y, -20, 250),
      z: clamp(rig.target.z, -300, 300),
    },
  };
}

export function dampCamera(current: CameraRig, desired: CameraRig, dt: number, reduced = false): CameraRig {
  const goal = clampCamera(desired);
  if (reduced) return goal;
  const delta = Math.max(0, Math.min(.1, dt));
  const alpha = 1 - Math.exp(-5 * delta);
  const yawAlpha = 1 - Math.exp(-2.5 * delta);
  const mix = (a: number, b: number) => a + (b - a) * alpha;
  const yaw = current.yaw + angleDifference(goal.yaw, current.yaw) * yawAlpha;
  return {
    yaw: Math.atan2(Math.sin(yaw), Math.cos(yaw)),
    elevation: mix(current.elevation, goal.elevation), span: mix(current.span, goal.span),
    target: {
      x: mix(current.target.x, goal.target.x), y: mix(current.target.y, goal.target.y),
      z: mix(current.target.z, goal.target.z),
    },
  };
}

/** Camera distance is exclusively render space, never orbital metres. */
export function cameraEye(rig: CameraRig, aspect = 1.5): Vector3 {
  const distance = rig.span / (2 * Math.tan(CAMERA_FOV * Math.PI / 360) * Math.min(Math.max(aspect, .5), 1.5));
  const horizontal = distance * Math.cos(rig.elevation);
  return {
    x: rig.target.x + horizontal * Math.sin(rig.yaw),
    y: rig.target.y + distance * Math.sin(rig.elevation),
    z: rig.target.z + horizontal * Math.cos(rig.yaw),
  };
}
