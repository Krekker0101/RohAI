import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';

test('real uploaded recording: neural detections, complete progress, preview and report', async ({ page }) => {
  test.skip(!process.env.ROHAI_VIDEO_ACCEPTANCE_FILE, 'Requires explicitly supplied local video and native model');
  test.setTimeout(180000);
  await page.goto('/#video');
  await page.getByLabel('Режим распознавания').selectOption(process.env.ROHAI_VIDEO_PROFILE ?? 'standard');
  if (!process.env.ROHAI_VIDEO_ROADS) await page.getByRole('checkbox', { name: /Подсвечивать дороги/ }).uncheck();
  await expect(page.getByRole('heading', { name: 'Анализ видеофайла' })).toBeVisible();
  await page.getByLabel('Выбрать видео для анализа').setInputFiles(process.env.ROHAI_VIDEO_ACCEPTANCE_FILE!);
  await expect(page.getByRole('button', { name: 'Загрузить и анализировать' })).toBeEnabled();
  await page.getByRole('button', { name: 'Загрузить и анализировать' }).click();
  await expect(page.getByLabel('Прогресс анализа видео')).toBeVisible();
  await expect(page.locator('.analysis-motion')).toBeVisible();
  await page.screenshot({ path: path.join(process.env.ROHAI_VIDEO_ARTIFACTS!, 'analysis-in-progress.png'), fullPage: true });
  await expect(page.getByRole('heading', { name: 'Анализ завершён' })).toBeVisible({ timeout: 120000 });
  await expect(page.locator('.analysis-motion')).toHaveCount(0);
  await expect(page.getByLabel('Прогресс анализа видео')).toHaveAttribute('value', '100');
  await expect(page.getByRole('img', { name: /Проанализированный кадр/ })).toBeVisible();
  if (process.env.ROHAI_VIDEO_ROADS) await expect(page.locator('.road-legend')).toBeVisible();
  await expect(page.getByText('VIDEO WORKSPACE', { exact: true })).toBeVisible();
  await expect(page.getByText('Поток и треки синтетические.', { exact: false })).toHaveCount(0);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать отчёт' }).click();
  const download = await downloadPromise;
  await download.saveAs(path.join(process.env.ROHAI_VIDEO_ARTIFACTS!, 'browser-report.json'));
  await page.getByRole('button', { name: 'Режим презентации' }).click();
  await expect(page.getByRole('heading', { name: 'Анализ видеофайла' })).toBeHidden();
  await page.screenshot({ path: path.join(process.env.ROHAI_VIDEO_ARTIFACTS!, 'presentation.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Что видит система' })).toBeVisible();
  await page.screenshot({ path: path.join(process.env.ROHAI_VIDEO_ARTIFACTS!, 'mobile.png'), fullPage: true });
});

async function mockVideo(page: Page, failure = false) {
  let uploaded = false;
  let cancelled = false;
  const identifier = 'a'.repeat(32);
  const row = () => ({
    id: identifier, filename: 'fixture.mp4', status: cancelled ? 'cancelled' : failure ? 'failed' : 'analyzing',
    stage: cancelled ? 'cancelled' : failure ? 'failed' : 'analyzing',
    error: failure ? 'VIDEO_METADATA_INVALID' : null, processed_frames: 5, total_frames: 20,
    progress_percent: 25, media_seconds: 0.5, duration_seconds: 2, fps: 10,
    preview_version: 0, unique_tracks: 1, counts: { car: 1 }, objects: [], explanations: [], timeline: [],
  });
  await page.route('**/api/v1/video/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/capabilities')) {
      await route.fulfill({ json: { max_upload_mb: 256, max_duration_seconds: 1800,
        vision_dependencies_available: true, model_available: true, aerial_model_available: true, road_segmentation_available: true } });
    } else if (url.pathname.endsWith('/cancel')) {
      cancelled = true;
      await route.fulfill({ json: row() });
    } else if (url.pathname.endsWith('/jobs')) {
      if (route.request().method() === 'POST') {
        expect(route.request().headers()['content-type']).toMatch(/^(video\/|application\/octet-stream$)/);
        expect(route.request().postDataBuffer()?.length).toBeGreaterThan(0);
        uploaded = true;
        await route.fulfill({ status: 202, json: row() });
      } else await route.fulfill({ json: uploaded ? [row()] : [] });
    } else await route.fulfill({ json: row() });
  });
}

test('software fixture: upload displays server progress and cancellation', async ({ page }) => {
  await mockVideo(page);
  await page.goto('/#video');
  await page.getByLabel('Выбрать видео для анализа').setInputFiles({
    name: 'fixture.mp4', mimeType: 'video/mp4', buffer: Buffer.from('SOFTWARE_FIXTURE'),
  });
  await page.getByRole('button', { name: 'Загрузить и анализировать' }).click();
  await expect(page.getByLabel('Прогресс анализа видео')).toHaveAttribute('value', '25');
  await expect(page.locator('.analysis-motion')).toBeVisible();
  await expect(page.locator('.analysis-scan')).toBeVisible();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.analysis-orbit')).toHaveCSS('animation-name', 'none');
  await expect(page.getByText('1 объектов в текущем кадре')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Скачать отчёт' })).toBeDisabled();
  await page.getByRole('button', { name: 'Остановить анализ' }).click();
  await expect(page.getByRole('heading', { name: 'Анализ отменён' })).toBeVisible();
  await expect(page.locator('.analysis-motion')).toHaveCount(0);
});

test('software fixture: damaged upload displays failure without successful report', async ({ page }) => {
  await mockVideo(page, true);
  await page.goto('/#video');
  await page.getByLabel('Выбрать видео для анализа').setInputFiles({
    name: 'fixture.mp4', mimeType: 'application/octet-stream', buffer: Buffer.from('INVALID_VIDEO'),
  });
  await page.getByRole('button', { name: 'Загрузить и анализировать' }).click();
  await expect(page.getByRole('heading', { name: 'Анализ остановлен с ошибкой' })).toBeVisible();
  await expect(page.locator('.analysis-motion')).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText('Файл не удалось прочитать как видео');
  await expect(page.getByRole('button', { name: 'Скачать отчёт' })).toBeDisabled();
});

test('software fixture: backend loss hides activity animation without inventing completion', async ({ page }) => {
  await mockVideo(page);
  await page.goto('/#video');
  await page.getByLabel('Выбрать видео для анализа').setInputFiles({
    name: 'fixture.mp4', mimeType: 'video/mp4', buffer: Buffer.from('SOFTWARE_FIXTURE'),
  });
  await page.getByRole('button', { name: 'Загрузить и анализировать' }).click();
  await expect(page.locator('.analysis-motion')).toBeVisible();
  await page.route(`**/api/v1/video/jobs/${'a'.repeat(32)}`, route => route.fulfill({
    status: 503, json: { detail: 'Backend disconnected fixture' },
  }));
  await expect(page.getByRole('alert')).toContainText('Backend disconnected fixture');
  await expect(page.locator('.analysis-motion')).toHaveCount(0);
  await expect(page.getByLabel('Прогресс анализа видео')).toHaveAttribute('value', '25');
});
