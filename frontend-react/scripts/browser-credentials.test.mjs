import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { initializeTestCredentials, testCredentials } from '../e2e/support/credentials.mjs';

test('each isolated run has unique credentials, shared unchanged with workers', () => {
  const env = {};
  const first = initializeTestCredentials(env);
  assert.deepEqual(initializeTestCredentials(env), first);
  assert.deepEqual(testCredentials({ ...env }), first);
  const next = initializeTestCredentials({});
  assert.notEqual(next.password, first.password);
  assert.notEqual(next.sdnToken, first.sdnToken);
  assert.notEqual(first.password, first.sdnToken);
  assert.ok(first.password.length >= 32);
});

test('operator credentials are not silently used by isolated tests', () => {
  const operatorPassword = randomBytes(32).toString('base64url');
  const env = { AJ_ADMIN_USERNAME: 'operator', AJ_ADMIN_PASSWORD: operatorPassword };
  assert.throws(() => testCredentials(env), /isolated service credentials/);
  const isolated = initializeTestCredentials(env);
  assert.notEqual(isolated.username, env.AJ_ADMIN_USERNAME);
  assert.notEqual(isolated.password, env.AJ_ADMIN_PASSWORD);
  assert.equal(env.AJ_ADMIN_PASSWORD, operatorPassword);
});

test('standalone tools load private harness credentials and explicit overrides', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'flare-browser-auth-'));
  try {
    const expected = initializeTestCredentials({});
    const filename = path.join(directory, 'credentials.json');
    writeFileSync(filename, JSON.stringify(expected), { mode: 0o600 });
    const env = { FLARE_TEST_CREDENTIALS_FILE: filename };
    assert.deepEqual(testCredentials(env), expected);
    assert.deepEqual(initializeTestCredentials(env), expected);
    assert.equal(testCredentials({ ...env, FLARE_TEST_OPERATOR: 'explicit' }).username, 'explicit');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
