import { test, expect } from '@playwright/test';

test('pilot reports do not present demo as field evidence', async ({ page }) => {
  await page.goto('/dashboard/#operations');
  await expect(page.getByRole('heading', { name: 'Отчёт наблюдений' })).toBeVisible();
  await expect(page.getByText(/Отчёты доступны при подключении к реальному backend/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Экспорт отчёта' })).toBeDisabled();
});

test('reports load separate domains, preserve unknowns and export', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('smart-traffic-data-source', 'real'));
  await page.route('**/api/v1/operations/report?days=*', route => route.fulfill({ json: {
    since: '2026-10-11T00:00:00Z', until: '2026-10-12T00:00:00Z',
    domains: [{ domain: 'LIVE_CAMERA', samples: 100, mean_queue_objects: null,
      peak_queue_objects: null, mean_observed_wait_seconds: null, mean_fps: 12,
      ready_samples: 0, measured_queue_samples: 0 }],
    alerts: [{ domain: 'LIVE_CAMERA', reason: 'observation_unavailable', samples: 100 }],
    methodology: 'Independent observation domains',
  } }));
  await page.goto('/dashboard/#operations');
  await expect(page.getByRole('cell', { name: 'LIVE_CAMERA', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Нет измерений', exact: true })).toHaveCount(3);
  await expect(page.getByText(/observation_unavailable — 100/)).toBeVisible();
  await page.getByRole('combobox', { name: 'Период отчёта' }).selectOption('7');
  await expect(page.getByRole('button', { name: 'Экспорт отчёта' })).toBeEnabled();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт отчёта' }).click();
  expect((await download).suggestedFilename()).toBe('rohai-report-7d.json');
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('disabled report service shows a useful error', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('smart-traffic-data-source', 'real'));
  await page.route('**/api/v1/operations/report?days=*', route => route.fulfill({
    status: 404, json: { detail: 'Not Found' },
  }));
  await page.goto('/dashboard/#operations');
  await expect(page.getByRole('alert')).toContainText('404');
  await expect(page.getByRole('button', { name: 'Экспорт отчёта' })).toBeDisabled();
});
