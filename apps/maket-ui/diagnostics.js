const ageSeconds = (timestamp, now) =>
  typeof timestamp === 'number' && Number.isFinite(timestamp)
    ? Math.max(0, now / 1000 - timestamp)
    : Infinity;

const showRate = value => typeof value === 'number' && Number.isFinite(value)
  ? value.toFixed(1) : '—';

export function pipelineDiagnosis(state, now = Date.now()) {
  const pipeline = state?.pipeline ?? {};
  const rates = state?.performance?.rates ?? {};
  const latency = state?.performance?.latency_ms?.camera_to_analytics?.p95;
  const heartbeats = state?.heartbeats ?? {};
  const status = state?.camera_connection;
  let cause;
  if (state?.connection_lost) cause = 'Нет связи с сервером';
  else if (status !== 'CONNECTED') cause = 'Камера не передаёт кадры';
  else if (pipeline.state === 'failed') cause = `Сбой конвейера${pipeline.error ? `: ${pipeline.error}` : ''}`;
  else if (pipeline.state === 'starting' && !heartbeats.frame) cause = 'Запуск камеры и модели';
  else if (ageSeconds(heartbeats.frame, now) > 3) cause = 'Кадры с камеры перестали поступать';
  else if (ageSeconds(heartbeats.inference, now) > 3) cause = 'Распознавание не выдаёт результат';
  else if (ageSeconds(heartbeats.analytics_update, now) > 3) cause = 'Аналитика не обновляется';
  else cause = 'Все этапы обновляются';
  const p95 = typeof latency === 'number' && Number.isFinite(latency)
    ? `${latency.toFixed(0)} мс` : '—';
  return `${cause} · кадры ${showRate(rates.capture_fps)} FPS · модель ${showRate(rates.inference_fps)} FPS · аналитика ${showRate(rates.analytics_fps)} FPS · камера→аналитика p95 ${p95} · пропуски ${pipeline.dropped_capture ?? 0}/${pipeline.dropped_inference ?? 0}/${pipeline.dropped_analysis ?? 0}`;
}
