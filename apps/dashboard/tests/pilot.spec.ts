import { test, expect } from '@playwright/test';
import path from 'node:path';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

for (const backgroundNoise of [false, true]) {
test(`microphone audio ends and reaches transcription with background noise ${backgroundNoise}`, async ({ page }) => {
  await page.addInitScript((backgroundNoise: boolean) => {
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext();
      await context.resume();
      const destination = context.createMediaStreamDestination();
      const source = context.createOscillator();
      const gain = context.createGain();
      gain.gain.value = backgroundNoise ? 0.06 : 0.008;
      source.connect(gain); gain.connect(destination);
      source.start(); source.stop(context.currentTime + 1.2);
      if (backgroundNoise) {
        const noise = context.createOscillator();
        const noiseGain = context.createGain(); noiseGain.gain.value = 0.006;
        noise.frequency.value = 120; noise.connect(noiseGain); noiseGain.connect(destination);
        noise.start(); noise.stop(context.currentTime + 10);
      }
      destination.stream.getTracks()[0].addEventListener('ended', () => void context.close());
      return destination.stream;
    };
  }, backgroundNoise);
  let recording: Buffer | null = null;
  await page.route('**/api/v1/pilot/**', async route => {
    if (route.request().url().endsWith('/capabilities')) await route.fulfill({ json: { chat_available: true, stt_available: true, tts_languages: ['ru'] } });
    else if (route.request().url().includes('/transcribe?')) {
      recording = route.request().postDataBuffer();
      await route.fulfill({ json: { text: 'Как загрузить видео?' } });
    } else if (route.request().url().endsWith('/chat')) await route.fulfill({ json: { conversation_id: '00000000-0000-4000-8000-000000000001', response_id: '00000000-0000-4000-8000-000000000002', answer: 'Проверка записи.', outcome: 'grounded', language: 'ru', sources: [] } });
    else await route.fulfill({ status: 204 });
  });
  await page.goto('/#video');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await page.getByRole('button', { name: 'Отключить звук Пилота' }).click();
  await page.getByRole('button', { name: 'Задать вопрос голосом' }).click();
  await expect.poll(() => recording !== null).toBe(true);
  const wav = recording as unknown as Buffer;
  expect(wav.readUInt32LE(24)).toBe(16000);
  let peak = 0;
  for (let offset = 44; offset < wav.length; offset += 2) peak = Math.max(peak, Math.abs(wav.readInt16LE(offset)) / 32768);
  expect(peak).toBeGreaterThan(0.05);
  await expect(page.getByRole('log', { name: 'Диалог с Пилотом' })).toContainText('Как загрузить видео?');
});
}

test('silent body states and mouth follow actual PCM playback and stop together', async ({ page }) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => new Promise<MediaStream>(() => {});
  });
  const sampleRate = 16000;
  const pcm = Buffer.alloc(sampleRate * 2 * 4);
  for (let index = 0; index < pcm.length / 2; index++) {
    if (index >= sampleRate * 0.6 && index < sampleRate * 3.4) {
      pcm.writeInt16LE(Math.round(Math.sin(index * 2 * Math.PI * 220 / sampleRate) * 6000), index * 2);
    }
  }
  await page.route('**/api/v1/pilot/**', async route => {
    if (route.request().url().endsWith('/capabilities')) await route.fulfill({ json: { chat_available: true, stt_available: true, tts_languages: ['ru', 'en'] } });
    else if (route.request().url().endsWith('/chat')) {
      await new Promise(resolve => setTimeout(resolve, 700));
      await route.fulfill({ json: { conversation_id: '00000000-0000-4000-8000-000000000001', response_id: '00000000-0000-4000-8000-000000000002', answer: 'Тестовый ответ для проверки аудиоанализа.', outcome: 'grounded', language: 'ru', sources: [] } });
    } else if (route.request().url().endsWith('/speech')) await route.fulfill({ contentType: 'application/x-ndjson', body: `${JSON.stringify({ audio: pcm.toString('base64'), sample_rate: sampleRate })}\n{"done":true}\n` });
    else await route.fulfill({ status: 204 });
  });
  await page.goto('/#video');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  const canvas = page.locator('.pilot-body-canvas');
  await expect(canvas).toHaveAttribute('data-body', 'thinking');
  await expect(canvas).toHaveAttribute('data-mouth-level', '0.000');
  await page.getByRole('button', { name: 'Задать вопрос голосом' }).click();
  await expect(canvas).toHaveAttribute('data-body', 'listening');
  await expect(canvas).toHaveAttribute('data-mouth-level', '0.000');
  await page.getByRole('button', { name: 'Остановить Пилота' }).click();
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как загрузить видео?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(canvas).toHaveAttribute('data-body', 'thinking');
  await expect(canvas).toHaveAttribute('data-body', 'explaining');
  await expect(canvas).toHaveAttribute('data-mouth-level', '0.000');
  await expect.poll(async () => Number(await canvas.getAttribute('data-mouth-level'))).toBeGreaterThan(0.05);
  await expect.poll(async () => Number(await canvas.getAttribute('data-frame'))).toBeGreaterThan(9);
  await page.screenshot({ path: path.resolve('../../artifacts/pilot-ai/animated-speaking.png') });
  await page.getByRole('button', { name: 'Остановить Пилота' }).click();
  await expect(canvas).toHaveAttribute('data-mouth-level', '0.000');
});

test('reduced motion holds the silent body still and leaves the mouth closed', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#video');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  const canvas = page.locator('.pilot-body-canvas');
  await expect(canvas).toHaveAttribute('data-frame', '0');
  await expect(canvas).toHaveAttribute('data-mouth-level', '0.000');
  await page.screenshot({ path: path.resolve('../../artifacts/pilot-ai/animated-idle.png') });
});

for (const interruption of ['visibility', 'mute'] as const) {
  test(`video workspace keeps a pending answer during ${interruption}`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('smart-traffic-data-source', 'real'));
    await page.route('**/api/v1/pilot/**', async route => {
      if (route.request().url().endsWith('/capabilities')) {
        await route.fulfill({ json: { chat_available: true, stt_available: false, tts_languages: [] } });
      } else if (route.request().url().endsWith('/chat')) {
        const payload = route.request().postDataJSON();
        expect(payload.page).toBe('video');
        expect(payload.mode).toBe('real');
        await new Promise(resolve => setTimeout(resolve, 500));
        await route.fulfill({ json: {
          conversation_id: '00000000-0000-4000-8000-000000000001',
          response_id: '00000000-0000-4000-8000-000000000002',
          answer: 'Откройте анализ видео и выберите запись.',
          outcome: 'grounded', language: 'ru', sources: [],
        } });
      } else await route.fulfill({ status: 204 });
    });
    await page.goto('/#video');
    await page.getByRole('button', { name: 'Открыть Пилота' }).click();
    if (interruption === 'visibility') await page.getByRole('button', { name: 'Отключить звук Пилота' }).click();
    await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как загрузить видео?');
    await page.getByRole('button', { name: 'Отправить' }).click();
    if (interruption === 'visibility') {
      await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', { configurable: true, value: true });
        document.dispatchEvent(new Event('visibilitychange'));
      });
    } else await page.getByRole('button', { name: 'Отключить звук Пилота' }).click();
    await expect(page.locator('.pilot-answer')).toContainText('выберите запись');
    await expect(page.getByText('Ответ остановлен вами', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('alert')).toHaveCount(0);
    if (interruption === 'visibility') await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.locator('.pilot-answer')).toBeVisible();
  });
}

test('an empty cancelled server response shows a retry error instead of a pending answer', async ({ page }) => {
  await page.route('**/api/v1/pilot/**', route => route.fulfill(route.request().url().endsWith('/capabilities')
    ? { json: { chat_available: true, stt_available: false, tts_languages: [] } }
    : { status: 204 }));
  await page.goto('/#video');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как загрузить видео?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.getByRole('alert')).toContainText('Запрос отменён сервером');
  await expect(page.locator('.pilot-pending')).toContainText('Не удалось получить ответ');
});

test('local Pilot answers in the dashboard with verified evidence and stays usable on mobile', async ({ page }) => {
  test.skip(!process.env.ROHAI_PILOT_NATIVE, 'Requires installed local Pilot models and live backend');
  test.setTimeout(240000);
  await page.goto('/#overview');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByText('ЛОКАЛЬНЫЙ AI')).toBeVisible();
  await page.getByRole('button', { name: 'Отключить звук Пилота' }).click();
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как загрузить видео?');
  const generatedResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/pilot/chat') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.getByText(/Проверяю сведения/)).toBeVisible();
  await expect(page.locator('.pilot-answer p')).not.toHaveText('', { timeout: 180000 });
  const generated = await (await generatedResponse).json();
  expect(generated.answer_origin).toBe('local-neural-generation');
  expect(generated.answer.length).toBeGreaterThan(10);
  await page.locator('.pilot-answer summary').click();
  await expect(page.locator('.pilot-source').filter({ hasText: 'Импорт видео' })).toBeVisible();
  await expect(page.locator('.pilot-source code').first()).toContainText('apps/');
  await page.screenshot({ path: path.resolve('../../artifacts/pilot-ai/browser-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Очистить диалог' }).click();
  await expect(page.locator('.pilot-turn')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Вопрос Пилоту' })).toBeVisible();
  await page.screenshot({ path: path.resolve('../../artifacts/pilot-ai/browser-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Остановить Пилота' }).click();
});

test('local Pilot streams audible PCM and follows mute', async ({ page }) => {
  test.skip(!process.env.ROHAI_PILOT_NATIVE, 'Requires installed local Pilot models and live backend');
  test.setTimeout(240000);
  await page.goto('/#overview');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как загрузить видео?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.locator('.pilot-answer')).toBeVisible({ timeout: 180000 });
  await expect(page.locator('.pilot-hero')).toHaveAttribute('style', /--voice: 0\.[1-9]/, { timeout: 30000 });
  await expect.poll(async () => Number(await page.locator('.pilot-body-canvas').getAttribute('data-mouth-level'))).toBeGreaterThan(0.05);
  await page.getByRole('button', { name: 'Отключить звук Пилота' }).click();
  await expect(page.getByRole('button', { name: 'Включить звук Пилота' })).toBeVisible();
  await expect(page.locator('.pilot-body-canvas')).toHaveAttribute('data-mouth-level', '0.000');
});

test('local Whisper transcribes a human voice fixture through the actual API', async ({ page }) => {
  const fixture = path.resolve('../../.runtime/pilot-fixtures/human-en.flac');
  test.skip(!process.env.ROHAI_PILOT_NATIVE || !existsSync(fixture), 'Requires the explicit human voice fixture');
  test.setTimeout(240000);
  await page.goto('/#overview');
  const compressed = readFileSync(fixture).toString('base64');
  const result = await page.evaluate(async base64 => {
    const bytes = Uint8Array.from(atob(base64), value => value.charCodeAt(0));
    const context = new AudioContext();
    const decoded = await context.decodeAudioData(bytes.buffer);
    const count = Math.min(Math.floor(decoded.duration * 16000), 19 * 16000);
    const offline = new OfflineAudioContext(1, count, 16000);
    const source = offline.createBufferSource(); source.buffer = decoded;
    source.connect(offline.destination); source.start();
    const rendered = await offline.startRendering();
    const samples = rendered.getChannelData(0);
    const wav = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(wav);
    const label = (offset: number, value: string) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
    label(0, 'RIFF'); view.setUint32(4, wav.byteLength - 8, true); label(8, 'WAVE'); label(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    label(36, 'data'); view.setUint32(40, samples.length * 2, true);
    samples.forEach((sample, index) => view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true));
    await context.close();
    const response = await fetch('/api/v1/pilot/transcribe?language=en', { method: 'POST', body: wav, headers: { 'Content-Type': 'audio/wav' } });
    return { status: response.status, duration: decoded.duration, payload: await response.json() };
  }, compressed);
  expect(result.status).toBe(200);
  expect(result.payload.text.trim().length).toBeGreaterThan(5);
  writeFileSync(path.resolve('../../artifacts/pilot-ai/human-stt.json'), JSON.stringify(result, null, 2));
});

test('microphone permission denial leaves the text channel available', async ({ page, context }) => {
  test.skip(!process.env.ROHAI_PILOT_NATIVE, 'Requires live backend');
  await context.grantPermissions([], { origin: process.env.ROHAI_VIDEO_BASE_URL ?? 'http://127.0.0.1:8000' });
  await page.goto('/#overview');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await page.getByRole('button', { name: 'Задать вопрос голосом' }).click();
  await expect(page.getByRole('alert')).toContainText('микрофону');
  await expect(page.getByRole('textbox', { name: 'Вопрос Пилоту' })).toBeEnabled();
});

test('stopping an inflight local answer keeps the next question usable', async ({ page }) => {
  test.skip(!process.env.ROHAI_PILOT_NATIVE, 'Requires installed local Pilot models and live backend');
  test.setTimeout(240000);
  await page.goto('/#overview');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await page.getByRole('button', { name: 'Отключить звук Пилота' }).click();
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как загрузить видео?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.locator('.pilot-pending')).toContainText('Проверяю');
  await page.getByRole('button', { name: 'Остановить Пилота' }).click();
  await expect(page.locator('.pilot-pending')).toContainText('Ответ остановлен');
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как выделяется дорога?');
  await page.getByRole('button', { name: 'Отправить' }).click();
  await expect(page.locator('.pilot-answer')).toContainText('дорог', { timeout: 180000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('REAL video workspace answers preset and typed questions using the local model', async ({ page }) => {
  test.skip(!process.env.ROHAI_PILOT_NATIVE, 'Requires installed local Pilot models and live backend');
  test.setTimeout(240000);
  await page.addInitScript(() => localStorage.setItem('smart-traffic-data-source', 'real'));
  await page.goto('/#video');
  await page.getByRole('button', { name: 'Открыть Пилота' }).click();
  await page.getByRole('button', { name: 'Как загрузить видео?', exact: true }).click();
  await expect(page.locator('.pilot-answer')).toContainText('видео', { timeout: 180000 });
  await expect(page.locator('.pilot-hero')).toHaveAttribute('style', /--voice: 0\.[1-9]/, { timeout: 30000 });
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).fill('Как выделяется дорога?');
  await page.getByRole('textbox', { name: 'Вопрос Пилоту' }).press('Enter');
  await expect(page.locator('.pilot-answer')).toHaveCount(2, { timeout: 180000 });
  await expect(page.locator('.pilot-answer').last()).toContainText('дорог');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('Ответ остановлен вами', { exact: true })).toHaveCount(0);
});
