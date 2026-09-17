/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { angleDifference, BOUNDS, createDrone, createWorld, length, reconcileFleet, SEPARATION, STEP, stepWorld } from './dynamics.ts';
import type { DroneMotion, MissionWorld, Vector3 } from './dynamics.ts';
import { advanceClock, createClock, frameClock, resetClock } from './clock.ts';
import { interpolateDrone, projectPosition } from './projection.ts';

const distance = (a: Vector3, b: Vector3) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
function run(world: MissionWorld, seconds: number) {
  for (let i = 0; i < Math.round(seconds / STEP); i++) world = stepWorld(world);
  return world;
}
function checkLimits(before: DroneMotion, after: DroneMotion) {
  assert.ok(length(after.velocity) <= after.maxSpeed + 1e-8, `${after.id}: speed ${length(after.velocity)}`);
  assert.ok(length(after.acceleration) <= after.maxAcceleration + 1e-8, 'acceleration magnitude');
  assert.ok(distance(before.velocity, after.velocity) <= after.maxAcceleration * STEP + 1e-8, 'actual velocity delta');
  assert.ok(Math.abs(angleDifference(after.heading, before.heading)) <= after.maxTurnRate * STEP + 1e-8, 'heading rate');
  assert.ok(Math.abs(after.velocity.y) <= after.maxClimbRate + 1e-8, 'climb/descent rate');
  assert.ok(after.position.y >= BOUNDS.minAltitude - 1e-8 && after.position.y <= BOUNDS.maxAltitude + 1e-8, `altitude ${after.position.y}`);
  assert.ok(Math.abs(after.position.x) <= BOUNDS.x && Math.abs(after.position.z) <= BOUNDS.z, `bounds ${JSON.stringify(after.position)}`);
  assert.ok(distance(before.position, after.position) <= after.maxSpeed * STEP + 1e-8, 'no teleport');
  assert.ok(Math.abs(after.roll) <= 0.24 + 1e-8, 'restrained bank');
}

test('initial state is deterministic, ID-derived, independent of fleet order, and deconflicted', () => {
  const ids = Array.from({ length: 32 }, (_, i) => `drone_${i + 1}`);
  assert.deepEqual(createWorld(ids), createWorld([...ids].reverse()));
  assert.deepEqual(createDrone('drone_1'), createDrone('drone_1'));
  assert.notDeepEqual(createDrone('drone_1').position, createDrone('drone_2').position);
  const world = createWorld(ids);
  for (let i = 0; i < world.drones.length; i++) for (let j = i + 1; j < world.drones.length; j++) {
    assert.ok(distance(world.drones[i].position, world.drones[j].position) >= SEPARATION, 'spawn separation');
  }
});

test('fleet reconciliation preserves trajectories and supports all IDs beyond five', () => {
  const ids = Array.from({ length: 50 }, (_, i) => `uav_${i}`);
  const world = run(createWorld(ids), 1);
  assert.equal(world.drones.length, 50);
  assert.strictEqual(reconcileFleet(world, [...ids].reverse()), world);
  const updated = reconcileFleet(world, [...ids.slice(1), 'new_uav']);
  for (const drone of world.drones.filter(d => d.id !== ids[0])) {
    assert.strictEqual(updated.drones.find(d => d.id === drone.id), drone);
  }
  assert.ok(!updated.drones.some(d => d.id === ids[0]));
  assert.equal(reconcileFleet(world, []).drones.length, 0);
});

test('pure steps do not mutate inputs', () => {
  const world = createWorld(['alpha', 'bravo']);
  const saved = structuredClone(world);
  stepWorld(world);
  assert.deepEqual(world, saved);
});

test('ten-minute patrol respects speed, acceleration, turn, altitude and world limits on every step', () => {
  let world = createWorld(Array.from({ length: 16 }, (_, i) => `patrol_${i}`));
  let transitions = 0;
  for (let i = 0; i < 600 / STEP; i++) {
    const next = stepWorld(world);
    next.drones.forEach((drone, j) => {
      checkLimits(world.drones[j], drone);
      if (drone.waypoint !== world.drones[j].waypoint) transitions++;
    });
    world = next;
  }
  assert.ok(transitions > 300, 'aircraft make sustained progress through patrol waypoints');
});

test('heading follows velocity and rolls damp to level after a turn', () => {
  let drone = { ...createDrone('bank_test'), position: { x: 0, y: 80, z: 0 }, velocity: { x: 8, y: 0, z: 0 }, heading: 0, roll: 0.2 };
  drone = { ...drone, waypoints: [{ x: 280, y: 80, z: 0 }], waypoint: 0 };
  const result = run({ drones: [drone], tick: 0 }, 3).drones[0];
  assert.ok(Math.abs(result.roll) < 0.001);
  assert.ok(Math.abs(angleDifference(result.heading, Math.atan2(result.velocity.z, result.velocity.x))) < 1e-9);
});

test('boundary return brakes and turns continuously instead of wrapping', () => {
  const drone = { ...createDrone('return_test'), position: { x: 290, y: 80, z: 0 }, velocity: { x: 12, y: 0, z: 0 }, heading: 0 };
  let world: MissionWorld = { drones: [drone], tick: 0 };
  let returned = false;
  for (let i = 0; i < 20 / STEP; i++) {
    const next = stepWorld(world);
    checkLimits(world.drones[0], next.drones[0]);
    if (next.drones[0].velocity.x < -2) returned = true;
    world = next;
  }
  assert.ok(returned);
  assert.ok(world.drones[0].position.x < 290);
});

test('altitude stopping envelope handles climbs and descents near the volume edges', () => {
  for (const upward of [false, true]) {
    const drone = { ...createDrone(`altitude_${upward}`),
      position: { x: 0, y: upward ? 125 : 40, z: 0 },
      velocity: { x: 8, y: upward ? 2 : -2, z: 0 }, heading: 0,
      waypoints: [{ x: 100, y: upward ? 200 : 0, z: 0 }], waypoint: 0,
    };
    let world: MissionWorld = { drones: [drone], tick: 0 };
    for (let i = 0; i < 20 / STEP; i++) {
      const next = stepWorld(world);
      checkLimits(world.drones[0], next.drones[0]);
      world = next;
    }
  }
});

test('predictive separation avoids a head-on encounter with bounded acceleration', () => {
  const a = { ...createDrone('alpha'), position: { x: -60, y: 80, z: 0 }, velocity: { x: 12, y: 0, z: 0 }, heading: 0,
    waypoints: [{ x: 220, y: 80, z: 0 }], waypoint: 0 };
  const b = { ...createDrone('bravo'), position: { x: 60, y: 80, z: 0 }, velocity: { x: -12, y: 0, z: 0 }, heading: Math.PI,
    waypoints: [{ x: -220, y: 80, z: 0 }], waypoint: 0 };
  let world: MissionWorld = { drones: [a, b], tick: 0 };
  let minimum = Infinity;
  let avoided = false;
  for (let i = 0; i < 12 / STEP; i++) {
    const next = stepWorld(world);
    next.drones.forEach((d, j) => checkLimits(world.drones[j], d));
    minimum = Math.min(minimum, distance(next.drones[0].position, next.drones[1].position));
    avoided ||= next.drones.some(d => d.mode === 'avoiding');
    world = next;
  }
  assert.ok(avoided);
  assert.ok(minimum > 12, `closest approach ${minimum}m`);
});

test('dense patrol separation remains collision-free across repeated crossings', () => {
  let world = createWorld(Array.from({ length: 24 }, (_, i) => `dense_${i}`));
  let minimum = Infinity;
  for (let i = 0; i < 180 / STEP; i++) {
    world = stepWorld(world);
    for (let a = 0; a < world.drones.length; a++) for (let b = a + 1; b < world.drones.length; b++) {
      minimum = Math.min(minimum, distance(world.drones[a].position, world.drones[b].position));
    }
  }
  assert.ok(minimum > 8, `closest patrol approach ${minimum}m`);
});

test('fixed-step results match at 30, 60, 120 Hz and irregular frame partitions', () => {
  const initial = createWorld(['alpha', 'bravo', 'charlie']);
  const simulate = (deltas: number[]) => deltas.reduce((clock, delta) => advanceClock(clock, delta), createClock(initial));
  const reference = simulate(Array(600).fill(1 / 60));
  for (const deltas of [Array(300).fill(1 / 30), Array(1200).fill(1 / 120), Array.from({ length: 200 }, () => [0.011, 0.019, 0.02]).flat()]) {
    const clock = simulate(deltas);
    assert.equal(clock.world.tick, 600);
    assert.deepEqual(clock.world, reference.world);
  }
});

test('pause, resume, tab suspension and huge frame gaps cannot accumulate catch-up time', () => {
  let clock = createClock(createWorld(['alpha']));
  clock = frameClock(clock, 1000);
  clock = frameClock(clock, 1016.6666667);
  const atPause = clock.world;
  clock = frameClock(clock, 5000, true);
  clock = frameClock(clock, 200000, true);
  assert.strictEqual(clock.world, atPause);
  clock = frameClock(clock, 900000);
  assert.strictEqual(clock.world, atPause, 'resume establishes time origin');
  clock = frameClock(clock, 900016.6666667);
  assert.equal(clock.world.tick, atPause.tick + 1);
  clock = frameClock(resetClock(clock), 990000);
  const beforeGap = clock.world.tick;
  clock = frameClock(clock, 999999);
  assert.equal(clock.world.tick, beforeGap + 6, 'at most 100ms of work');
  assert.equal(advanceClock(clock, Number.NaN).world.tick, clock.world.tick);
  assert.equal(advanceClock(clock, -1).world.tick, clock.world.tick);
});

test('interpolation follows the shortest heading arc and never alters simulation', () => {
  const a = { ...createDrone('alpha'), heading: Math.PI - 0.01 };
  const b = { ...a, heading: -Math.PI + 0.01, position: { ...a.position, x: a.position.x + 1 } };
  const halfway = interpolateDrone(a, b, 0.5);
  assert.ok(Math.abs(halfway.heading - Math.PI) < 1e-10);
  assert.equal(halfway.position.x, a.position.x + 0.5);
  assert.deepEqual(projectPosition(a.position), projectPosition({ ...a.position }));
});
