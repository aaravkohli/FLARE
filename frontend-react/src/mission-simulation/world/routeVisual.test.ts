/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { curvePoint, routeCurve } from './networkGeometry.ts';
import { requestedAnnotation, routeAppearance, routeLegRange } from './routeVisual.ts';

test('route styles preserve installed, requested, hidden alternatives and authoritative HOLD priority', () => {
  const base = { installed: false, candidate: false, hold: false };
  assert.equal(routeAppearance(base, true, true, false), 'hidden');
  assert.equal(routeAppearance(base, true, true, true), 'alternative');
  assert.equal(routeAppearance(base, true, false, true), 'hidden');
  assert.equal(routeAppearance({ ...base, candidate: true }, true, true, false), 'requested');
  assert.equal(routeAppearance({ ...base, installed: true }, true, true, false), 'installed');
  assert.equal(routeAppearance({ ...base, installed: true, hold: true }, true, true, false), 'hidden');
  assert.equal(routeAppearance({ ...base, candidate: true, hold: true }, true, true, true), 'inactive');
  assert.equal(routeAppearance({ ...base, installed: true }, false, true, true), 'hidden');
  assert.equal(requestedAnnotation(false), 'REQUESTED · NOT CONFIRMED');
  assert.equal(requestedAnnotation(true), 'REQUESTED · FAILED');
});

test('satellite route is two exact moving legs while Direct remains one', () => {
  const drone = { x: 30, y: 80, z: -20 };
  const satellite = { x: 120, y: 330, z: -300 };
  const mast = { x: -260, y: 0, z: 157 };
  const direct = routeCurve(drone, mast);
  const via = routeCurve(drone, mast, satellite);
  assert.deepEqual(routeLegRange('direct', 0), [0, 1]);
  assert.deepEqual(curvePoint(direct, routeLegRange('direct', 0)[1]), mast);
  assert.deepEqual(routeLegRange('satellite', 0), [0, .5]);
  assert.deepEqual(routeLegRange('satellite', 1), [.5, 1]);
  assert.deepEqual(curvePoint(via, routeLegRange('satellite', 0)[0]), drone);
  assert.deepEqual(curvePoint(via, routeLegRange('satellite', 0)[1]), satellite);
  assert.deepEqual(curvePoint(via, routeLegRange('satellite', 1)[1]), mast);
});
