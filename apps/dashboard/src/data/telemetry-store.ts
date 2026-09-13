import { appendHistory, normalize, trustedSignals, type HistoryPoint, type Snapshot, type SystemInfo } from '../domain/telemetry';
import { request } from './api';

export interface EventItem { id: number; time: number; phase: string; stage: string; emergency: boolean }
interface State {
  snapshot: Snapshot | null; system: SystemInfo | null; connected: boolean; safe: boolean;
  ready: boolean; receivedAt: number; error: string | null; history: HistoryPoint[]; events: EventItem[];
}
export class TelemetryStore {
  private state: State = { snapshot: null, system: null, connected: false, safe: false, ready: false, receivedAt: 0, error: null, history: [], events: [] };
  private listeners = new Set<() => void>();
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private controller: AbortController | null = null;
  private active = false;
  private attempt = 0;
  private pending: Snapshot | null = null;
  private lastSequence = -1;
  private eventId = 0;
  private checking = false;
  private lastHealth = 0;
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(patch: Partial<State>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()); }
  start = () => {
    if (this.active) return;
    this.active = true;
    this.controller = new AbortController();
    void this.health(); this.connect();
    this.timer = setInterval(() => {
      if (this.pending) { this.accept(this.pending); this.pending = null; }
      const safe = trustedSignals(this.state.connected, this.state.ready, this.state.receivedAt, Date.now(), this.state.system?.stale_after_seconds ?? 3, this.state.snapshot);
      if (safe !== this.state.safe) this.update({ safe });
      if (Date.now() - this.lastHealth > 2500) void this.health();
    }, 250);
  };
  stop = () => {
    this.active = false; this.controller?.abort();
    if (this.timer) clearInterval(this.timer);
    if (this.retry) clearTimeout(this.retry);
    this.socket?.close(); this.socket = null; this.pending = null;
  };
  refresh = async () => { await this.health(); };
  private async health() {
    if (this.checking || !this.active) return;
    this.checking = true; this.lastHealth = Date.now();
    try {
      const signal = AbortSignal.any([this.controller!.signal, AbortSignal.timeout(5000)]);
      const [system, health] = await Promise.all([
        request<SystemInfo>('/api/v1/system', 'GET', undefined, signal),
        fetch('/health/ready', { signal, cache: 'no-store' }),
      ]);
      if (this.active) this.update({ system, ready: health.ok });
    } catch { if (this.active) this.update({ ready: false }); }
    finally { this.checking = false; }
  }
  private connect() {
    if (!this.active) return;
    const url = new URL('/ws/telemetry', location.href); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(url); this.socket = ws; this.lastSequence = -1;
    ws.onopen = () => { if (this.socket === ws && this.active) this.update({ connected: true }); };
    ws.onmessage = event => {
      if (!this.active || this.socket !== ws) return;
      try {
        const snapshot = normalize(JSON.parse(String(event.data)));
        if (snapshot.sequence <= this.lastSequence) return;
        this.lastSequence = snapshot.sequence; this.pending = snapshot; this.attempt = 0;
        this.update({ receivedAt: Date.now(), error: null });
      } catch (error) { this.update({ error: String(error), ready: false, safe: false }); ws.close(); }
    };
    ws.onclose = () => {
      if (!this.active || this.socket !== ws) return;
      if (this.pending) { this.accept(this.pending); this.pending = null; }
      this.update({ connected: false, safe: false });
      this.retry = setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.attempt++, 15000));
    };
    ws.onerror = () => ws.close();
  }
  private accept(snapshot: Snapshot) {
    const old = this.state.snapshot;
    const changed = !old || old.signals.phase !== snapshot.signals.phase || old.signals.stage !== snapshot.signals.stage || old.emergency !== snapshot.emergency;
    const restarted = old && snapshot.time < old.time;
    const events = restarted ? [] : this.state.events;
    this.update({ snapshot, history: appendHistory(this.state.history, snapshot), events: changed ? [{ id: this.eventId++, time: snapshot.time, phase: snapshot.signals.phase, stage: snapshot.signals.stage, emergency: snapshot.emergency !== null }, ...events].slice(0, 30) : events });
  }
}
export const telemetryStore = new TelemetryStore();
