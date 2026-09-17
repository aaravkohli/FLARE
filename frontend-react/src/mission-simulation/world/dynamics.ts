/** Visualization only. Metres, seconds and radians; Y is altitude.
 * No FLARE telemetry, policy, selection or network inputs belong in this module.
 */
export interface Vector3 { x: number; y: number; z: number }
export interface DroneMotion {
  id: string;
  position: Vector3;
  velocity: Vector3;
  acceleration: Vector3;
  heading: number;
  pitch: number;
  roll: number;
  target: Vector3;
  waypoints: readonly Vector3[];
  waypoint: number;
  maxSpeed: number;
  maxAcceleration: number;
  maxTurnRate: number;
  maxClimbRate: number;
  mode: 'patrol' | 'avoiding' | 'returning';
}
export interface MissionWorld { drones: readonly DroneMotion[]; tick: number }
export const STEP = 1 / 60;
export const MAX_FRAME_DELTA = 0.1;
export const BOUNDS = { x: 360, z: 240, minAltitude: 35, maxAltitude: 130 } as const;
export const SEPARATION = 24;
const NEIGHBOR_RANGE = 160;
const clamp = (v: number, low: number, high: number) => Math.max(low, Math.min(high, v));
export const angleDifference = (a: number, b: number) => Math.atan2(Math.sin(a - b), Math.cos(a - b));
export const length = (v: Vector3) => Math.hypot(v.x, v.y, v.z);
const subtract = (a: Vector3, b: Vector3): Vector3 => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });
const zero = (): Vector3 => ({ x: 0, y: 0, z: 0 });

function hash(id: string): number {
  let value = 2166136261;
  for (let i = 0; i < id.length; i++) value = Math.imul(value ^ id.charCodeAt(i), 16777619);
  return value >>> 0;
}
const fraction = (id: string, salt: string) => hash(`${id}:${salt}`) / 4294967296;

export function createDrone(id: string, phaseOffset = 0): DroneMotion {
  const cx = (fraction(id, 'cx') - 0.5) * 70;
  const cz = (fraction(id, 'cz') - 0.5) * 45;
  const rx = 155 + fraction(id, 'rx') * 55;
  const rz = 100 + fraction(id, 'rz') * 40;
  const altitude = 52 + fraction(id, 'altitude') * 55;
  // Static polygonal patrol geometry, not time-driven sine motion.
  const waypoints = Array.from({ length: 12 }, (_, i) => {
    const angle = i * Math.PI * 2 / 12;
    return { x: cx + rx * Math.cos(angle), y: altitude + (i % 4 < 2 ? 4 : -4), z: cz + rz * Math.sin(angle) };
  });
  const phase = (fraction(id, 'phase') + phaseOffset) % 1 * waypoints.length;
  const start = Math.floor(phase);
  const waypoint = (start + 1) % waypoints.length;
  const a = waypoints[start];
  const b = waypoints[waypoint];
  const t = phase - start;
  return {
    id, position: { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t },
    velocity: zero(), acceleration: zero(), heading: Math.atan2(b.z - a.z, b.x - a.x), pitch: 0, roll: 0,
    target: { ...b }, waypoints, waypoint, maxSpeed: 14 + fraction(id, 'speed') * 3,
    maxAcceleration: 4, maxTurnRate: 0.55, maxClimbRate: 2, mode: 'patrol',
  };
}

/** Sorted IDs make initialization and floating-point accumulation order stable.
 * Existing aircraft never reset when the fleet is reordered, renamed or extended.
 */
export function reconcileFleet(world: MissionWorld, ids: readonly string[]): MissionWorld {
  const sorted = [...new Set(ids)].sort();
  if (sorted.length === world.drones.length && sorted.every((id, i) => world.drones[i].id === id)) return world;
  const existing = new Map(world.drones.map(drone => [drone.id, drone]));
  const retained = world.drones.filter(drone => sorted.includes(drone.id));
  const drones = sorted.map(id => {
    const previous = existing.get(id);
    if (previous) return previous;
    let drone = createDrone(id);
    // Deterministic spawn deconfliction; only new entities get a spawn position.
    for (let attempt = 1; attempt <= 256 && retained.some(other => length(subtract(drone.position, other.position)) < SEPARATION); attempt++) {
      drone = createDrone(id, attempt * 0.61803398875 % 1);
    }
    retained.push(drone);
    return drone;
  });
  return { ...world, drones };
}
export const createWorld = (ids: readonly string[]): MissionWorld => reconcileFleet({ drones: [], tick: 0 }, ids);

function cell(v: Vector3): [number, number, number] {
  return [Math.floor(v.x / NEIGHBOR_RANGE), Math.floor(v.y / NEIGHBOR_RANGE), Math.floor(v.z / NEIGHBOR_RANGE)];
}

function advanceDrone(drone: DroneMotion, neighbors: readonly DroneMotion[]): DroneMotion {
  const p = drone.position;
  let waypoint = drone.waypoint;
  const speed = Math.hypot(drone.velocity.x, drone.velocity.z);
  let target = drone.waypoints[waypoint];
  const lookahead = Math.max(18, speed * 2.4);
  if (Math.hypot(target.x - p.x, target.z - p.z) < lookahead) {
    waypoint = (waypoint + 1) % drone.waypoints.length;
    target = drone.waypoints[waypoint];
  }
  const distance = Math.hypot(target.x - p.x, target.z - p.z);
  let desiredX = (target.x - p.x) / Math.max(distance, 1) * drone.maxSpeed;
  let desiredZ = (target.z - p.z) / Math.max(distance, 1) * drone.maxSpeed;
  let desiredY = clamp((target.y - p.y) * 0.45, -drone.maxClimbRate, drone.maxClimbRate);
  let avoiding = false;
  let separationSpeed = drone.maxSpeed;
  for (const other of neighbors) {
    if (other.id === drone.id) continue;
    const offset = subtract(p, other.position);
    const relative = subtract(drone.velocity, other.velocity);
    const nowDistance = length(offset);
    if (nowDistance > NEIGHBOR_RANGE) continue;
    const relativeSpeed2 = relative.x ** 2 + relative.y ** 2 + relative.z ** 2;
    const closestTime = clamp(-(offset.x * relative.x + offset.y * relative.y + offset.z * relative.z) / Math.max(relativeSpeed2, 0.001), 0, 6);
    const future = { x: offset.x + relative.x * closestTime, y: offset.y + relative.y * closestTime, z: offset.z + relative.z * closestTime };
    const futureDistance = length(future);
    if (nowDistance >= SEPARATION * 3 && futureDistance >= SEPARATION * 2) continue;
    avoiding = true;
    const urgency = Math.max(clamp((SEPARATION * 2 - nowDistance) / SEPARATION, 0, 1), clamp((SEPARATION * 1.5 - futureDistance) / SEPARATION, 0, 1));
    // Symmetric encounters use a deterministic right-hand pass. No random nudges.
    const sideX = -Math.sin(drone.heading);
    const sideZ = Math.cos(drone.heading);
    const repel = Math.max(nowDistance, 0.01);
    desiredX += urgency * drone.maxSpeed * (offset.x / repel * 2 + sideX);
    desiredZ += urgency * drone.maxSpeed * (offset.z / repel * 2 + sideZ);
    desiredY += urgency * (drone.id < other.id ? 1 : -1) * drone.maxClimbRate;
    const ahead = -(offset.x * Math.cos(drone.heading) + offset.z * Math.sin(drone.heading));
    if (ahead > 0 && (futureDistance < SEPARATION * 2 || nowDistance < SEPARATION * 2)) {
      // Reserve braking distance for BOTH approaching aircraft. Turning alone
      // cannot resolve a crowded crossing with bounded acceleration.
      separationSpeed = Math.min(separationSpeed, Math.sqrt(Math.max(0, nowDistance - SEPARATION) * drone.maxAcceleration * 0.5));
    }
  }

  let returning = false;
  // Predictive braking/return zone, far inside the hard volume. Positions are
  // integrated continuously; they are never wrapped, clamped or teleported.
  for (const axis of ['x', 'z'] as const) {
    const margin = 35 + speed * speed / drone.maxAcceleration;
    const weight = clamp((Math.abs(p[axis]) - (BOUNDS[axis] - margin - 40)) / 40, 0, 1);
    if (weight > 0) {
      returning = true;
      const inward = -Math.sign(p[axis]) * drone.maxSpeed;
      if (axis === 'x') desiredX = desiredX * (1 - weight) + inward * weight;
      else desiredZ = desiredZ * (1 - weight) + inward * weight;
    }
  }
  // Vertical stopping envelope, with reserve for discrete integration.
  desiredY = clamp(desiredY,
    -Math.min(drone.maxClimbRate, Math.sqrt(Math.max(0, p.y - BOUNDS.minAltitude - 0.25) * 1.5)),
    Math.min(drone.maxClimbRate, Math.sqrt(Math.max(0, BOUNDS.maxAltitude - 0.25 - p.y) * 1.5)));
  const vy = drone.velocity.y + clamp(desiredY - drone.velocity.y, -STEP, STEP);
  const verticalAcceleration = (vy - drone.velocity.y) / STEP;
  const horizontalBudget = Math.sqrt(Math.max(0, drone.maxAcceleration ** 2 - verticalAcceleration ** 2)) * STEP;
  const desiredHeading = Math.atan2(desiredZ, desiredX);
  const headingError = angleDifference(desiredHeading, drone.heading);
  const heading = drone.heading + clamp(headingError, -drone.maxTurnRate * STEP, drone.maxTurnRate * STEP);
  const targetSpeed = Math.min(separationSpeed, Math.hypot(desiredX, desiredZ)) * Math.max(0, Math.cos(headingError));
  const nextSpeed = speed + clamp(targetSpeed - speed, -horizontalBudget * 0.7, horizontalBudget * 0.7);
  let vx = Math.cos(heading) * nextSpeed;
  let vz = Math.sin(heading) * nextSpeed;
  const dv = Math.hypot(vx - drone.velocity.x, vz - drone.velocity.z);
  if (dv > horizontalBudget) {
    const fraction = horizontalBudget / dv;
    vx = drone.velocity.x + (vx - drone.velocity.x) * fraction;
    vz = drone.velocity.z + (vz - drone.velocity.z) * fraction;
  }
  // Horizontal and vertical controls share the same 3D speed budget.
  const horizontalSpeed = Math.hypot(vx, vz);
  const maxHorizontal = Math.sqrt(Math.max(0, drone.maxSpeed ** 2 - vy ** 2));
  // Previous state is admissible; the desired velocity is projected before the
  // acceleration limiter below, preserving both limits on climbs.
  if (horizontalSpeed > maxHorizontal) {
    vx *= maxHorizontal / horizontalSpeed;
    vz *= maxHorizontal / horizontalSpeed;
  }
  const candidate = { x: vx, y: vy, z: vz };
  const change = subtract(candidate, drone.velocity);
  const scale = Math.min(1, drone.maxAcceleration * STEP / Math.max(length(change), 1e-12));
  const velocity = { x: drone.velocity.x + change.x * scale, y: drone.velocity.y + change.y * scale, z: drone.velocity.z + change.z * scale };
  const newHeading = Math.hypot(velocity.x, velocity.z) > 1e-8 ? Math.atan2(velocity.z, velocity.x) : heading;
  const turnRate = angleDifference(newHeading, drone.heading) / STEP;
  const rollTarget = clamp(Math.atan2(turnRate * speed, 9.81), -0.24, 0.24);
  const pitchTarget = clamp(Math.atan2(velocity.y, Math.max(speed, 2)), -0.16, 0.16);
  const damping = 1 - Math.exp(-STEP * 3);
  return {
    ...drone, waypoint, target, velocity,
    position: { x: p.x + velocity.x * STEP, y: p.y + velocity.y * STEP, z: p.z + velocity.z * STEP },
    acceleration: { x: (velocity.x - drone.velocity.x) / STEP, y: (velocity.y - drone.velocity.y) / STEP, z: (velocity.z - drone.velocity.z) / STEP },
    heading: newHeading, roll: drone.roll + (rollTarget - drone.roll) * damping,
    pitch: drone.pitch + (pitchTarget - drone.pitch) * damping,
    mode: returning ? 'returning' : avoiding ? 'avoiding' : 'patrol',
  };
}

/** One pure fixed step, with all avoidance evaluated against the same snapshot. */
export function stepWorld(world: MissionWorld): MissionWorld {
  const grid = new Map<string, DroneMotion[]>();
  for (const drone of world.drones) {
    const key = cell(drone.position).join(',');
    const bucket = grid.get(key) || [];
    bucket.push(drone);
    grid.set(key, bucket);
  }
  return { tick: world.tick + 1, drones: world.drones.map(drone => {
    const [cx, cy, cz] = cell(drone.position);
    const neighbors: DroneMotion[] = [];
    for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
      neighbors.push(...(grid.get(`${cx + x},${cy + y},${cz + z}`) || []));
    }
    return advanceDrone(drone, neighbors);
  }) };
}
