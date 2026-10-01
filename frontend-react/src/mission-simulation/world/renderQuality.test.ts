/// <reference types="node" />
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AUTO_BALANCED_REPORTS, createAutoQualityDecision, renderPixelRatio } from './renderQuality.ts';

test('balanced quality lowers raster cost at both standard and high device DPR', () => {
  assert.equal(renderPixelRatio('high', 1), 1);
  assert.equal(renderPixelRatio('balanced', 1), .85);
  assert.equal(renderPixelRatio('high', 2), 1.5);
  assert.equal(renderPixelRatio('balanced', 2), 1);
  assert.equal(renderPixelRatio('high', Number.NaN), 1);
});

test('automatic downgrade needs eight consecutive low visible-frame reports', () => {
  const decision = createAutoQualityDecision();
  for (let i = 0; i < AUTO_BALANCED_REPORTS - 1; i++) {
    assert.equal(decision.observe(i * 1000, 39, 'high', false), false);
  }
  assert.equal(decision.observe((AUTO_BALANCED_REPORTS - 1) * 1000, 39, 'high', false), true);
  assert.equal(decision.observe(AUTO_BALANCED_REPORTS * 1000, 39, 'balanced', false), false);
});

test('recovery, report gaps, pauses and manual High cancel a low-FPS streak', () => {
  const decision = createAutoQualityDecision();
  for (let i = 0; i < 7; i++) decision.observe(i * 1000, 40, 'high', false);
  assert.equal(decision.observe(7000, 45, 'high', false), false);
  assert.equal(decision.observe(8000, 40, 'high', false), false);
  assert.equal(decision.observe(11000, 40, 'high', false), false);
  decision.reset(); // Hidden, paused, resize, or a new warmup.
  assert.equal(decision.observe(12000, 40, 'high', false), false);
  assert.equal(decision.observe(13000, 40, 'high', true), false);
  for (let i = 0; i < 12; i++) assert.equal(decision.observe(14000 + i * 1000, 35, 'high', true), false);
});
