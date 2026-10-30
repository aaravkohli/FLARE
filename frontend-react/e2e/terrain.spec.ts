import { testCredentials } from './support/credentials.mjs';
const { username: testOperator, password: testPassword } = testCredentials();
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import { resetMission, missionSetting } from './workspace-helpers';

const directory = process.env.FLARE_ARTIFACT_DIR || '/private/tmp/flare-terrain-review';
fs.mkdirSync(directory, { recursive: true });

test('field station layers, quality, cameras and responsive views preserve the paused mission', async ({ page, request }) => {
  test.setTimeout(120000);
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/#mission');
  await page.getByPlaceholder('Operator').fill(testOperator); await page.getByPlaceholder('Password').fill(testPassword);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  const center = page.getByRole('region', { name: 'Swarm command center' }); await expect(center).toBeVisible();
  await resetMission(center); await missionSetting(center, 'Fleet size', '10');
  const auth = await request.post('http://127.0.0.1:18000/auth/token', { form: { username: testOperator, password: testPassword } });
  const headers = { Authorization: `Bearer ${(await auth.json()).access_token}` };
  const snapshot = async () => (await request.get('http://127.0.0.1:18000/mission/simulation', { headers })).json();
  const before = await snapshot();
  await center.getByRole('button', { name: '3D world', exact: true }).click();
  const canvas = center.locator('canvas.mission-world-canvas');
  await expect(canvas).toHaveAttribute('data-terrain-material', 'dem-derived-maps');
  await expect(canvas).toHaveAttribute('data-site-details', 'true');
  await expect(canvas).toHaveAttribute('data-site-buildings', '8'); await expect(canvas).toHaveAttribute('data-site-pads', '3');
  await expect(canvas).toHaveAttribute('data-site-woodlands', '6'); await expect(canvas).toHaveAttribute('data-site-solar-panels', '32');
  await center.getByRole('button', { name: 'Layers', exact: true }).click();
  const site = center.getByRole('checkbox', { name: 'Site details', exact: true }); await expect(site).toBeChecked();
  await site.focus(); await page.keyboard.press('Space'); await expect(canvas).toHaveAttribute('data-site-details', 'false');
  await page.keyboard.press('Escape'); await expect(center.getByRole('button', { name: 'Layers', exact: true })).toBeFocused();
  await center.screenshot({ path: `${directory}/site-details-off.png` });
  await center.getByRole('button', { name: 'Layers', exact: true }).click(); await site.check();
  await center.getByRole('button', { name: 'Close Layers', exact: true }).click();
  await page.screenshot({ path: `${directory}/terrain-close.png` });
  await center.getByRole('button', { name: 'View settings', exact: true }).click();
  await center.getByLabel('Mission visual quality').selectOption('high'); await expect(canvas).toHaveAttribute('data-quality', 'high');
  await center.getByRole('dialog', { name: 'View settings', exact: true }).getByText('Simulation scope', { exact: true }).click();
  await expect(center.getByText(/USGS 3DEP elevation core/)).toContainText('illustrative');
  await center.getByLabel('Mission visual quality').selectOption('balanced'); await expect(canvas).toHaveAttribute('data-quality', 'balanced');
  await center.getByRole('button', { name: 'Close View settings', exact: true }).click();
  for (const action of ['Mission range', 'Overview', 'Tactical', 'Follow selected UAV', 'Network camera', 'Fit full network', 'Fit fleet', 'Rotate camera right', 'Rotate camera left']) {
    await center.getByRole('button', { name: 'Camera', exact: true }).click(); await center.getByRole('button', { name: action, exact: true }).click();
    await center.getByRole('button', { name: 'Close Camera', exact: true }).click();
    await expect(canvas).toBeVisible();
  }
  for (const width of [1440, 1085, 1024, 768, 687, 390]) {
    await page.setViewportSize({ width, height: width === 687 ? 765 : 900 }); await canvas.scrollIntoViewIfNeeded();
    await center.getByRole('button', { name: 'Camera', exact: true }).click(); await center.getByRole('button', { name: 'Mission range', exact: true }).click(); await center.getByRole('button', { name: 'Close Camera', exact: true }).click();
    await page.waitForTimeout(800); await page.screenshot({ path: `${directory}/terrain-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await center.getByRole('button', { name: 'Presentation', exact: true }).click();
  await center.getByRole('button', { name: 'Camera', exact: true }).click(); await center.getByRole('button', { name: 'Mission range', exact: true }).click(); await center.getByRole('button', { name: 'Close Camera', exact: true }).click();
  await page.waitForTimeout(1000); await page.screenshot({ path: `${directory}/terrain-overview.png` });
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1440, height: 620 });
  await page.screenshot({ path: `${directory}/terrain-short-desktop.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await center.getByRole('button', { name: 'Layers', exact: true }).click(); await site.uncheck(); await center.getByRole('button', { name: 'Close Layers', exact: true }).click();
  const retained: unknown[] = [];
  for (let i = 0; i < 3; i++) {
    await center.getByRole('button', { name: '2D network', exact: true }).click();
    await expect(center.getByRole('img', { name: 'Authoritative 2D mission network' })).toBeVisible();
    await center.getByRole('button', { name: '3D world', exact: true }).click();
    await expect(canvas).toHaveAttribute('data-terrain-material', 'dem-derived-maps');
    await expect(canvas).toHaveAttribute('data-site-details', 'false');
    retained.push(await canvas.evaluate(c => ({ geometries: Number(c.dataset.geometries), textures: Number(c.dataset.textures) })));
  }
  expect(retained[1]).toEqual(retained[2]);
  const after = await snapshot();
  expect(after.session_id).toBe(before.session_id); expect(after.simulation_time).toBe(before.simulation_time);
  expect(after.nodes.map((n: { id: string; position: number[] }) => [n.id, n.position])).toEqual(before.nodes.map((n: { id: string; position: number[] }) => [n.id, n.position]));
  expect(after.statistics).toEqual(before.statistics);
  fs.writeFileSync(`${directory}/paused-continuity.json`, JSON.stringify({ session: after.session_id, simulation_time: after.simulation_time, renderer_resources: retained, authoritative_state_unchanged: true }, null, 2));
  expect(errors).toEqual([]);
});

test('missing surface maps retain readable terrain and reduced-motion site controls', async ({ page }) => {
  await page.route('**/*terrain-*.png', route => route.abort('failed')); await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#mission'); await page.getByPlaceholder('Operator').fill(testOperator); await page.getByPlaceholder('Password').fill(testPassword);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  const center = page.getByRole('region', { name: 'Swarm command center' });
  await center.getByRole('button', { name: '3D world', exact: true }).click();
  const canvas = center.locator('canvas.mission-world-canvas');
  await expect(canvas).toHaveAttribute('data-terrain-material', 'vertex-color'); await expect(canvas).toHaveAttribute('data-terrain-source', 'usgs-3dep');
  await expect(canvas).toHaveAttribute('data-site-details', 'true');
  await center.getByRole('button', { name: 'Layers', exact: true }).click(); await center.getByRole('checkbox', { name: 'Site details', exact: true }).uncheck();
  await expect(canvas).toHaveAttribute('data-site-details', 'false'); await center.getByRole('button', { name: 'Close Layers', exact: true }).click();
  await center.screenshot({ path: `${directory}/terrain-fallback-reduced-motion.png` });
});
