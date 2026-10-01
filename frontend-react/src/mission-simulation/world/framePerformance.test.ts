/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createFrameRateSampler, FRAME_RATE_WINDOW_MS } from './framePerformance.ts';

test('visible frames report a bounded rolling average and a low-frame percentile after warmup', () => {
  const sampler = createFrameRateSampler();
  let report = null as ReturnType<typeof sampler.record>;
  for (let frame = 0; frame <= 8 * 60; frame++) {
    const next = sampler.record(frame * 1000 / 60, true);
    if (next) report = next;
  }
  assert.ok(report);
  assert.ok(Math.abs(report.averageFps - 60) < .1);
  assert.ok(Math.abs(report.lowFps - 60) < .1);
  assert.ok(report.sampleCount <= FRAME_RATE_WINDOW_MS / (1000 / 60) + 1);
});

test('slow frames lower the fifth-percentile FPS beneath the recent average', () => {
  const sampler = createFrameRateSampler();
  let now = 0;
  sampler.record(now, true);
  for (let i = 0; i < 61; i++) sampler.record(now += 1000 / 60, true);
  for (let i = 0; i < 180; i++) sampler.record(now += 1000 / 60, true);
  let report = null as ReturnType<typeof sampler.record>;
  for (let i = 0; i < 35; i++) {
    const next = sampler.record(now += 1000 / 30, true);
    if (next) report = next;
  }
  assert.ok(report);
  assert.ok(report.lowFps >= 29 && report.lowFps <= 31);
  assert.ok(report.averageFps > report.lowFps && report.averageFps < 60);
});

test('a past slowdown ages out of the recent window', () => {
  const sampler = createFrameRateSampler();
  let now = 0;
  sampler.record(now, true);
  for (let i = 0; i < 61; i++) sampler.record(now += 1000 / 60, true);
  for (let i = 0; i < 45; i++) sampler.record(now += 1000 / 30, true);
  let report = null as ReturnType<typeof sampler.record>;
  for (let i = 0; i < 7 * 60; i++) {
    const next = sampler.record(now += 1000 / 60, true);
    if (next) report = next;
  }
  assert.ok(report);
  assert.ok(report.lowFps > 59 && report.averageFps > 59);
  assert.ok(report.sampleCount <= FRAME_RATE_WINDOW_MS / (1000 / 60) + 1);
});

test('pause or tab suspension clears history and requires a fresh warmup', () => {
  const sampler = createFrameRateSampler();
  let report = null as ReturnType<typeof sampler.record>;
  for (let frame = 0; frame <= 150; frame++) {
    const next = sampler.record(frame * 1000 / 60, true);
    if (next) report = next;
  }
  assert.ok(report);
  assert.equal(sampler.record(2600, false), null);
  assert.equal(sampler.record(10000, true), null);
  assert.equal(sampler.record(10800, true), null);
  assert.equal(sampler.record(11000, true), null);
  assert.equal(sampler.record(11900, true), null);
  const fresh = sampler.record(12000, true);
  assert.ok(fresh);
  assert.ok(fresh.sampleCount < report.sampleCount);
});
