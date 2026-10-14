import { test, expect } from '@playwright/test';

const artifacts = process.env.ROHAI_BROWSER_ARTIFACTS ?? '../../artifacts/rohai-ai-2.0/stage3';

test('AI workspace shows measured records and explicit Shadow boundaries', async ({ page }) => {
  await page.goto('/dashboard/#intelligence');
  await expect(page.getByRole('heading', { name: 'Verified intelligence', exact: true })).toBeVisible();
  await expect(page.getByText('Research Shadow approval allows observation only. Physical RL activation is blocked.')).toBeVisible();
  await page.getByRole('button', { name: 'MODEL TRAINING', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Actual training runs' })).toBeVisible();
  await page.getByRole('button', { name: 'MODEL REGISTRY', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Model Registry' })).toBeVisible();
  await page.getByRole('button', { name: 'AI COMPARISON', exact: true }).click();
  await expect(page.getByRole('heading', { name: /independent benchmark/ })).toBeVisible();
  await page.getByRole('button', { name: 'SHADOW AI', exact: true }).click();
  await expect(page.getByText(/counterfactual vehicle delay: NOT MEASURABLE/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start observation only' })).toBeDisabled();
  await page.getByRole('button', { name: 'AI DIAGNOSTICS', exact: true }).click();
  await expect(page.getByText('BLOCKED', { exact: true })).toBeVisible();
  await page.screenshot({ path: `${artifacts}/dashboard-diagnostics.png`, fullPage: true });
  await page.getByRole('button', { name: 'AI COMPARISON', exact: true }).click();
  await page.screenshot({ path: `${artifacts}/dashboard-comparison.png`, fullPage: true });
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(page.getByRole('heading', { name: /independent benchmark/ })).toBeVisible();
  await page.screenshot({ path: `${artifacts}/dashboard-tablet.png`, fullPage: true });
});
