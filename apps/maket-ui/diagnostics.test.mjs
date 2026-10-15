import test from 'node:test';
import assert from 'node:assert/strict';
import {pipelineDiagnosis} from './diagnostics.js';

const now = 10000;
const live = {
  camera_connection: 'CONNECTED', pipeline: {state: 'running', dropped_capture: 2},
  performance: {rates: {capture_fps: 10, inference_fps: 5, analytics_fps: 5},
    latency_ms: {camera_to_analytics: {p95: 120}}},
  heartbeats: {frame: 9.5, inference: 9.3, analytics_update: 9.1},
};

test('reports active stages and measured latency', () => {
  const result = pipelineDiagnosis(live, now);
  assert.match(result, /Все этапы обновляются/);
  assert.match(result, /модель 5\.0 FPS/);
  assert.match(result, /p95 120 мс/);
  assert.match(result, /пропуски 2\/0\/0/);
});

test('identifies first stalled stage', () => {
  assert.match(pipelineDiagnosis({...live, camera_connection: 'DISCONNECTED'}, now), /Камера не передаёт/);
  assert.match(pipelineDiagnosis({...live, heartbeats: {...live.heartbeats, frame: 6}}, now), /Кадры с камеры перестали/);
  assert.match(pipelineDiagnosis({...live, heartbeats: {...live.heartbeats, inference: 6}}, now), /Распознавание не выдаёт/);
  assert.match(pipelineDiagnosis({...live, heartbeats: {...live.heartbeats, analytics_update: 6}}, now), /Аналитика не обновляется/);
  assert.match(pipelineDiagnosis({...live, pipeline: {state: 'failed', error: 'test'}}, now), /Сбой конвейера: test/);
  assert.match(pipelineDiagnosis({...live, connection_lost: true}, now), /Нет связи с сервером/);
  assert.match(pipelineDiagnosis({...live, pipeline: {state: 'starting'}, heartbeats: {}}, now), /Запуск камеры и модели/);
});
