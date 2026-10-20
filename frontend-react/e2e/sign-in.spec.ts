import { test, expect } from '@playwright/test';
import fs from 'node:fs';

const directory = process.env.FLARE_ARTIFACT_DIR || '/private/tmp/flare-sign-in-review';
fs.mkdirSync(directory, { recursive: true });

test('sign-in is readable and keyboard accessible at desktop and narrow sizes', async ({ page }) => {
  await page.goto('/#mission');
  await expect(page.getByRole('heading', { name: 'Sign in to FLARE', exact: true })).toBeVisible();
  await expect(page.getByLabel('Operator', { exact: true })).toHaveAttribute('autocomplete', 'username');
  await expect(page.getByLabel('Password', { exact: true })).toHaveAttribute('autocomplete', 'current-password');
  for (const width of [390, 572, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: width < 800 ? 765 : 900 });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Sign in to FLARE', exact: true })).toBeVisible();
    await expect(page.locator('.login-brand')).toHaveCount(1);
    await page.screenshot({ path: `${directory}/sign-in-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    const button = await page.getByRole('button', { name: 'Sign in', exact: true }).boundingBox();
    expect(button!.height).toBeGreaterThanOrEqual(44);
    await expect(page.getByLabel('Password', { exact: true })).toBeInViewport();
  }
  await page.setViewportSize({ width: 572, height: 480 });
  await page.getByRole('button', { name: 'Sign in', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeInViewport();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.getByLabel('Operator', { exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Password', { exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Show password', exact: true })).toBeFocused();
});

test('password visibility and Caps Lock feedback preserve the entered value', async ({ page }) => {
  await page.goto('/');
  const password = page.getByLabel('Password', { exact: true });
  await password.fill('visibility-check');
  await expect(password).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: 'Show password', exact: true }).click();
  await expect(password).toHaveAttribute('type', 'text');
  await expect(password).toHaveValue('visibility-check');
  await expect(password).toBeFocused();
  await page.getByRole('button', { name: 'Hide password', exact: true }).click();
  await expect(password).toHaveAttribute('type', 'password');
  await password.focus();
  await password.dispatchEvent('keydown', { key: 'A', modifierCapsLock: true });
  await expect(page.getByText('Caps Lock is on.', { exact: true })).toBeVisible();
  await password.dispatchEvent('keyup', { key: 'A', modifierCapsLock: false });
  await expect(page.getByText('Caps Lock is on.', { exact: true })).toHaveCount(0);
});

test('pending, rejected and unavailable authentication remains explicit without duplicate submissions', async ({ page }) => {
  let submissions = 0;
  let release: (() => void) | undefined;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/auth/token', async route => {
    submissions++;
    await waiting;
    await route.fulfill({ status: 401, json: { detail: 'Invalid credentials' } });
  });
  await page.goto('/#mission');
  await page.getByLabel('Password', { exact: true }).fill('invalid-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Signing in…', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Operator', { exact: true })).toBeDisabled();
  await expect(page.getByLabel('Password', { exact: true })).toBeDisabled();
  await page.keyboard.press('Enter');
  expect(submissions).toBe(1);
  release!();
  await expect(page.getByRole('alert')).toContainText('Operator name or password is incorrect');
  await expect(page.getByLabel('Password', { exact: true })).toHaveValue('invalid-password');
  await page.screenshot({ path: `${directory}/sign-in-error.png` });
  await page.unroute('**/auth/token');
  await page.route('**/auth/token', route => route.abort('failed'));
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Unable to reach FLARE');
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeEnabled();
});

test('real authentication retains the requested Mission destination and Lock returns to sign-in', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/#mission');
  await page.getByLabel('Operator', { exact: true }).fill('admin');
  await page.getByLabel('Password', { exact: true }).fill('antijam2026');
  await page.getByLabel('Password', { exact: true }).press('Enter');
  await expect(page.getByRole('region', { name: 'Swarm command center', exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page).toHaveURL(/#mission$/);
  await page.getByRole('button', { name: 'Lock', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to FLARE', exact: true })).toBeVisible();
  await expect(page.getByLabel('Password', { exact: true })).toHaveAttribute('type', 'password');
  expect(errors).toEqual([]);
});
