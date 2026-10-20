import { angleDifference } from './dynamics.ts';
import type { DroneMotion, Vector3 } from './dynamics.ts';
import { GROUND_STATION_VISUAL_ENDPOINT } from './groundStation.ts';
import { terrainHeight } from './terrain.ts';

export const CAMERA_FOV = 44;
export type CameraMode = 'overview' | 'range' | 'tactical' | 'follow' | 'network';
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

/** Ground context and aircraft share the frame; display geometry stays fixed. */
export function rangeCamera(points: readonly Vector3[] = [], aspect = 1.5): CameraRig {
  const xs = [-435, 375, ...points.map(p => p.x)], zs = [-225, 315, ...points.map(p => p.z)];
  const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const x = (minX + maxX) / 2, z = (minZ + maxZ) / 2;
  const ground = terrainHeight(x, z), altitude = Math.max(100, ...points.map(p => p.y));
  const target = { x, y: (ground + altitude) / 2, z }, yaw = .28, elevation = radians(47);
  const landmarks: Vector3[] = [];
  for (const px of [-435, 375]) for (const pz of [-225, 315]) landmarks.push({ x: px, y: terrainHeight(px, pz), z: pz });
  const tan = Math.tan(CAMERA_FOV * Math.PI / 360), ratio = Math.max(.5, aspect);
  let distance = 600;
  for (const p of [...points, ...landmarks]) {
    const dx = p.x - target.x, dy = p.y - target.y, dz = p.z - target.z;
    const right = dx * Math.cos(yaw) - dz * Math.sin(yaw);
    const up = -dx * Math.sin(yaw) * Math.sin(elevation) + dy * Math.cos(elevation) - dz * Math.cos(yaw) * Math.sin(elevation);
    const towardEye = dx * Math.sin(yaw) * Math.cos(elevation) + dy * Math.sin(elevation) + dz * Math.cos(yaw) * Math.cos(elevation);
    distance = Math.max(distance, towardEye + Math.max(Math.abs(right) / (tan * ratio * .88), Math.abs(up) / (tan * .88)));
  }
  return { yaw, elevation, span: distance * 2 * tan * Math.min(ratio, 1.5), target };
}

/** Camera coordinates are local visual metres; the orbit and flight state are unchanged. */
export function cameraPreset(mode: CameraMode, selected?: CameraSubject, networkFrame: NetworkFrame = 'unknown'): CameraRig {
  if (mode === 'overview') return overview();
  if (mode === 'range') return rangeCamera();
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

export function clampCamera(rig: CameraRig, maxSpan=1350): CameraRig {
  return {
    yaw: Math.atan2(Math.sin(rig.yaw), Math.cos(rig.yaw)),
    elevation: clamp(rig.elevation, radians(25), Math.PI * .44),
    span: clamp(rig.span, 160, maxSpan),
    target: {
      x: clamp(rig.target.x, maxSpan>1350?-3000:-420, maxSpan>1350?3000:420),
      y: clamp(rig.target.y, -20, 250),
      z: clamp(rig.target.z, maxSpan>1350?-3000:-300, maxSpan>1350?3000:300),
    },
  };
}

export function dampCamera(current: CameraRig, desired: CameraRig, dt: number, reduced = false, maxSpan=1350): CameraRig {
  const goal = clampCamera(desired,maxSpan);
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
