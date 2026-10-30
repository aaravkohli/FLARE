import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';

/** Configure once in Playwright's parent process; workers and services inherit it. */
export function initializeTestCredentials(env = process.env) {
  const supplied = env.FLARE_TEST_CREDENTIALS_FILE
    ? JSON.parse(readFileSync(env.FLARE_TEST_CREDENTIALS_FILE, 'utf8')) : {};
  env.FLARE_TEST_OPERATOR ||= supplied.username || 'isolated-browser-operator';
  env.FLARE_TEST_PASSWORD ||= supplied.password || randomBytes(32).toString('base64url');
  env.FLARE_TEST_SDN_TOKEN ||= supplied.sdnToken || randomBytes(32).toString('base64url');
  return testCredentials(env);
}

/** Standalone tools may use the private credentials file emitted by the harness. */
export function testCredentials(env = process.env) {
  const supplied = env.FLARE_TEST_CREDENTIALS_FILE
    ? JSON.parse(readFileSync(env.FLARE_TEST_CREDENTIALS_FILE, 'utf8')) : {};
  const username = env.FLARE_TEST_OPERATOR || supplied.username;
  const password = env.FLARE_TEST_PASSWORD || supplied.password;
  const sdnToken = env.FLARE_TEST_SDN_TOKEN || supplied.sdnToken;
  if (!username || !password || !sdnToken) {
    throw new Error('Use Playwright or set FLARE_TEST_CREDENTIALS_FILE to the isolated service credentials file.');
  }
  return { username, password, sdnToken };
}
