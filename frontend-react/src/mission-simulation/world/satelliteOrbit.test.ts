/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createCircularOrbit, DEFAULT_SATELLITE_CONFIG, MISSION_SATELLITE_ORBIT, satelliteOrbitAt } from './satelliteOrbit.ts';
import { SATELLITE_SKY_BAND, satelliteRenderPosition } from './satelliteRender.ts';
import { advanceClock, createClock, frameClock, resetClock } from './clock.ts';
import { createWorld, reconcileFleet } from './dynamics.ts';
import { BASE, pathGeometry } from './projection.ts';

const near = (actual: number, expected: number, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} ≠ ${expected}`);
const orbit = MISSION_SATELLITE_ORBIT;
const norm = (p: { x: number; y: number; z: number }) => Math.hypot(p.x, p.y, p.z);

test('circular orbit is deterministic, non-mutating and has the configured phase at its epoch', () => {
  const config = { ...DEFAULT_SATELLITE_CONFIG, epochSeconds: 1000, initialPhaseRad: 0 };
  const custom = createCircularOrbit(config);
  const saved = structuredClone(config);
  assert.deepEqual(satelliteOrbitAt(custom, 1234), satelliteOrbitAt(createCircularOrbit(config), 1234));
  assert.deepEqual(config, saved);
  const initial = satelliteOrbitAt(custom, 1000);
  near(initial.positionM.x, custom.radiusM);
  near(initial.positionM.y, 0);
  near(initial.positionM.z, 0);
  const quarter = satelliteOrbitAt(custom, 1000 + custom.periodSeconds / 4);
  near(quarter.positionM.x, 0);
  near(quarter.positionM.y, custom.radiusM * Math.cos(config.inclinationRad));
  near(quarter.positionM.z, custom.radiusM * Math.sin(config.inclinationRad));
});

test('period is derived from radius and GM, not a demo animation duration', () => {
  const exact = createCircularOrbit({ earthRadiusM: 1, altitudeM: 3, gravitationalParameter: 4, inclinationRad: 0, initialPhaseRad: 0, epochSeconds: 0 });
  near(exact.angularVelocityRadS, 0.25);
  near(exact.periodSeconds, 8 * Math.PI);
  near(exact.speedMps, 1);
  near(orbit.radiusM, 6_921_000);
  assert.ok(orbit.periodSeconds > 95 * 60 && orbit.periodSeconds < 96 * 60);
  const initial = satelliteOrbitAt(orbit, 0);
  const complete = satelliteOrbitAt(orbit, orbit.periodSeconds);
  assert.deepEqual(complete, initial);
});

test('1× progression matches elapsed foreground time across frame partitions', () => {
  for (const hz of [30, 60, 120]) {
    let clock = createClock(createWorld([]));
    for (let i = 0; i < hz * 10; i++) clock = advanceClock(clock, 1 / hz);
    near(clock.simulationSeconds, 10);
    const state = satelliteOrbitAt(orbit, clock.simulationSeconds);
    near(state.phaseRad - orbit.config.initialPhaseRad, orbit.angularVelocityRadS * 10);
    assert.ok(state.phaseRad - orbit.config.initialPhaseRad < 0.012, 'a short demo cannot complete an orbit');
  }
});

test('pause/resume retains exact orbital phase including sub-step elapsed time', () => {
  let clock = createClock(createWorld(['drone_1']));
  clock = frameClock(clock, 1000);
  clock = frameClock(clock, 1013); // less than one physics step
  const before = satelliteOrbitAt(orbit, clock.simulationSeconds);
  assert.ok(clock.simulationSeconds > 0);
  clock = frameClock(clock, 50000, true);
  clock = frameClock(clock, 1000000, true);
  assert.deepEqual(satelliteOrbitAt(orbit, clock.simulationSeconds), before);
  clock = frameClock(clock, 2000000); // resume establishes the new wall-time origin
  assert.deepEqual(satelliteOrbitAt(orbit, clock.simulationSeconds), before);
  clock = frameClock(clock, 2000010);
  near(clock.simulationSeconds, 0.023);
  const resumed = satelliteOrbitAt(orbit, clock.simulationSeconds);
  near(resumed.phaseRad - before.phaseRad, orbit.angularVelocityRadS * 0.01);
  const reset = resetClock(clock);
  assert.equal(reset.simulationSeconds, clock.simulationSeconds);
  near(frameClock(frameClock(reset, 3e6), 4e6).simulationSeconds, clock.simulationSeconds + 0.1);
});

test('orbital progression does not depend on the fleet or UAV waypoint progress', () => {
  let empty = createClock(createWorld([]));
  let fleet = createClock(createWorld(['alpha', 'bravo']));
  for (let i = 0; i < 120; i++) {
    empty = advanceClock(empty, 1 / 60);
    fleet = advanceClock(fleet, 1 / 60);
    if (i === 50) fleet = { ...fleet, world: reconcileFleet(fleet.world, ['new_drone']) };
  }
  assert.deepEqual(satelliteOrbitAt(orbit, empty.simulationSeconds), satelliteOrbitAt(orbit, fleet.simulationSeconds));
});

test('compressed mapping is bounded and independent of literal orbital radius', () => {
  for (let i = 0; i <= 360; i++) {
    const state = satelliteOrbitAt(orbit, orbit.periodSeconds * i / 360);
    const point = satelliteRenderPosition(state);
    assert.ok(point.x >= SATELLITE_SKY_BAND.centerX - SATELLITE_SKY_BAND.halfWidth && point.x <= SATELLITE_SKY_BAND.centerX + SATELLITE_SKY_BAND.halfWidth);
    assert.ok(point.y >= 42 && point.y <= 66, 'satellite stays in the reserved sky band');
    const rescaled = satelliteRenderPosition({ direction: { x: state.direction.x * 1e6, y: state.direction.y * 1e6, z: state.direction.z * 1e6 } });
    near(point.x, rescaled.x, 1e-10);
    near(point.y, rescaled.y, 1e-10);
  }
  const otherRadius = createCircularOrbit({ ...DEFAULT_SATELLITE_CONFIG, altitudeM: 35_786_000 });
  assert.deepEqual(satelliteRenderPosition(satelliteOrbitAt(orbit, 0)), satelliteRenderPosition(satelliteOrbitAt(otherRadius, 0)));
});

test('rendered progress is subtle at 1× and links use the exact moving endpoint', () => {
  const first = satelliteRenderPosition(satelliteOrbitAt(orbit, 0));
  const later = satelliteRenderPosition(satelliteOrbitAt(orbit, 10));
  const displacement = Math.hypot(later.x - first.x, later.y - first.y);
  assert.ok(displacement > 0.1 && displacement < 2, `ten-second render displacement: ${displacement}`);
  const drone = { x: 400, y: 200 };
  const before = pathGeometry('satellite', drone, first);
  const after = pathGeometry('satellite', drone, later);
  assert.notEqual(before, after);
  assert.ok(after.includes(`${later.x} ${later.y} M ${later.x} ${later.y}`));
  assert.ok(after.endsWith(`${BASE.x} ${BASE.y}`));
  assert.equal(pathGeometry('direct', drone, first), pathGeometry('direct', drone, later));
  assert.equal(pathGeometry('mesh', drone, first, { x: 500, y: 200 }), pathGeometry('mesh', drone, later, { x: 500, y: 200 }));
});

test('long periods remain finite, circular and tangential without integration drift', () => {
  for (const time of [-1e15, -1e10, -orbit.periodSeconds, 0, 1e6, 1e10, 1e15]) {
    const state = satelliteOrbitAt(orbit, time);
    assert.ok(Object.values(state.positionM).every(Number.isFinite));
    assert.ok(Object.values(state.velocityMps).every(Number.isFinite));
    near(norm(state.positionM), orbit.radiusM, 1e-7);
    near(norm(state.velocityMps), orbit.speedMps, 1e-9);
    near(norm(state.direction), 1);
    const dot = state.positionM.x * state.velocityMps.x + state.positionM.y * state.velocityMps.y + state.positionM.z * state.velocityMps.z;
    near(dot, 0, 1e-4);
    assert.ok(Object.values(satelliteRenderPosition(state)).every(Number.isFinite));
  }
});

test('invalid configuration and time fail explicitly instead of generating NaN coordinates', () => {
  for (const patch of [{ altitudeM: -1 }, { earthRadiusM: 0 }, { gravitationalParameter: 0 }, { inclinationRad: 4 }, { initialPhaseRad: NaN }, { epochSeconds: Infinity }, { altitudeM: 1e200 }]) {
    assert.throws(() => createCircularOrbit({ ...DEFAULT_SATELLITE_CONFIG, ...patch }), RangeError);
  }
  for (const time of [NaN, Infinity, -Infinity]) assert.throws(() => satelliteOrbitAt(orbit, time), RangeError);
  assert.throws(() => satelliteRenderPosition({ direction: { x: 0, y: 0, z: 0 } }), RangeError);
  assert.throws(() => satelliteRenderPosition({ direction: { x: NaN, y: 1, z: 0 } }), RangeError);
});
