export const directions = ['north', 'south', 'east', 'west'] as const;
export type Direction = typeof directions[number];
export type Policy = 'adaptive' | 'fixed';
export type Lamp = 'red' | 'yellow' | 'green';
export const directionNames: Record<Direction, string> = { north: 'Север', south: 'Юг', east: 'Восток', west: 'Запад' };
export const phaseNames: Record<string, string> = { north_south: 'Север ↔ Юг', east_west: 'Восток ↔ Запад', pedestrian: 'Пешеходы' };
export const classNames = ['Легковые', 'Автобусы', 'Грузовики', 'Мотоциклы', 'Велосипеды', 'Пешеходы'];
export interface LaneMetrics {
  direction: Direction; counts: number[]; queue: number; score: number; wait: number;
  maxWait: number; arrival: number; congestion: string; length: number | null;
  lengthUnit: string; confidence: number | null;
}
export interface KPI {
  arrived: number; departed: number; remaining: number; total_wait_seconds: number;
  mean_wait_per_arrival_seconds: number; mean_completed_wait_seconds: number;
  throughput_per_minute: number; max_queue: number;
}
export interface Track {
  track_id: number; track_uid: string; kind: string; confidence: number; lane: string;
  first_seen: number; last_seen: number; speed_px_per_second: number | null;
  current_wait_time: number; queued: boolean; queue_confidence: number;
}
export interface Pipeline {
  state: string; captured: number; inferred: number; analyzed: number; published: number;
  dropped_capture: number; dropped_inference: number; dropped_analysis: number;
  processing_fps: number; latency_ms: number; error: string | null;
}
export interface Snapshot {
  sequence: number; mode: string; policy: Policy | null; time: number;
  lanes: LaneMetrics[]; lamps: Record<string, Lamp>;
  signals: { phase: string; stage: string; elapsed_seconds: number };
  target: number; reason: string; emergency: string | null; failure: string | null;
  kpi: KPI | null; pipeline: Pipeline | null; tracks: Track[];
  pedestrians: number; unique: number | null; source: string; hasTraffic: boolean;
}
export interface SystemInfo {
  mode: string; policy: Policy; hardware: string; operator_auth_required: boolean;
  source: string; source_id: string; stale_after_seconds: number;
  yellow_seconds: number; all_red_seconds: number;
}
export interface Comparison { seed: number; duration_seconds: number; fixed: KPI; adaptive: KPI; wait_reduction_percent: number | null }
type RecordValue = Record<string, unknown>;
function object(value: unknown): RecordValue {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('Некорректный формат телеметрии');
  return value as RecordValue;
}
function numeric(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('Некорректное значение телеметрии');
  return value;
}
export function normalize(value: unknown): Snapshot {
  const data = object(value);
  const vision = data.schema_version === '2.0-vision';
  if (!vision && data.schema_version !== '1.0') throw new Error('Версия телеметрии не поддерживается');
  const traffic = data.traffic === null ? null : object(data.traffic);
  const signals = object(data.signals);
  if (!['green', 'yellow', 'all_red'].includes(String(signals.stage)) || !phaseNames[String(signals.phase)]) throw new Error('Некорректный сигнал');
  numeric(signals.elapsed_seconds);
  const lamps = object(data.lamps);
  for (const key of [...directions, 'pedestrian']) {
    if (!['green', 'yellow', 'red'].includes(String(lamps[key]))) throw new Error('Некорректная команда светофора');
  }
  const lanes = traffic === null ? [] : directions.map((direction): LaneMetrics => {
    const row = vision
      ? object(object(traffic.directions)[direction])
      : object((traffic.approaches as RecordValue[]).find(a => a.direction === direction));
    const counts = vision ? row : object(row.counts);
    return {
      direction,
      counts: (vision ? ['car_count', 'bus_count', 'truck_count', 'motorcycle_count', 'bicycle_count', 'pedestrian_count'] : ['cars', 'buses', 'trucks', 'motorcycles', 'bicycles', 'pedestrians']).map(k => numeric(counts[k])),
      queue: numeric(row[vision ? 'queue_count' : 'queue_length']), score: numeric(row.traffic_score),
      wait: numeric(row[vision ? 'average_wait_seconds' : 'mean_wait_seconds']),
      maxWait: numeric(row[vision ? 'max_wait_seconds' : 'oldest_wait_seconds']),
      arrival: numeric(row[vision ? 'arrival_rate' : 'arrival_rate_per_minute']),
      congestion: String(row.congestion), length: vision ? numeric(row.estimated_queue_length) : null,
      lengthUnit: vision && row.queue_length_unit === 'm' ? 'м' : 'px',
      confidence: vision ? numeric(row.queue_confidence) : null,
    };
  });
  return {
    sequence: numeric(data.sequence), mode: vision ? String(data.mode) : 'simulation',
    policy: data.policy === 'adaptive' || data.policy === 'fixed' ? data.policy : null,
    time: traffic ? numeric(traffic[vision ? 'timestamp' : 'simulation_time']) : 0,
    lanes, lamps: lamps as Record<string, Lamp>, signals: signals as unknown as Snapshot['signals'],
    target: numeric(data.green_target_seconds), reason: String(object(data.decision).reason),
    emergency: typeof data.emergency_phase === 'string' ? data.emergency_phase : null,
    failure: typeof data.failure === 'string' ? data.failure : null,
    kpi: vision ? null : data.kpi as KPI, pipeline: vision ? data.pipeline as Pipeline : null,
    tracks: vision ? data.tracks as Track[] : [],
    pedestrians: traffic ? (vision ? lanes.reduce((sum, lane) => sum + lane.counts[5], 0) : numeric(traffic.pedestrians_waiting)) : 0,
    unique: traffic && vision ? numeric(traffic.unique_vehicle_arrivals) + numeric(traffic.unique_pedestrian_arrivals) : null,
    source: traffic && vision ? String(traffic.source_id) : 'simulation', hasTraffic: traffic !== null,
  };
}
export function totals(snapshot: Snapshot | null) {
  const lanes = snapshot?.lanes ?? [];
  const queue = lanes.reduce((sum, l) => sum + l.queue, 0);
  return {
    queue, score: lanes.reduce((sum, l) => sum + l.score, 0),
    wait: queue ? lanes.reduce((sum, l) => sum + l.wait * l.queue, 0) / queue : 0,
    arrival: lanes.reduce((sum, l) => sum + l.arrival, 0),
    vehicles: lanes.reduce((sum, l) => sum + l.counts.slice(0, 5).reduce((a, b) => a + b, 0), 0),
  };
}
export const format = (n: number | null | undefined, digits = 0): string => n == null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: digits });
export const clock = (seconds: number): string => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;

export interface HistoryPoint { time: number; queue: number; wait: number; score: number }
export function appendHistory(history: HistoryPoint[], snapshot: Snapshot): HistoryPoint[] {
  const previous = history.at(-1);
  if (previous && snapshot.time >= previous.time && snapshot.time - previous.time < 1) return history;
  const base = previous && snapshot.time < previous.time ? [] : history;
  return [...base.slice(-89), { time: snapshot.time, ...totals(snapshot) }];
}
export function trustedSignals(connected: boolean, ready: boolean, receivedAt: number, now: number, staleSeconds: number, snapshot: Snapshot | null) {
  return connected && ready && receivedAt > 0 && now - receivedAt < staleSeconds * 1000 && snapshot !== null && !snapshot.failure && snapshot.hasTraffic;
}
