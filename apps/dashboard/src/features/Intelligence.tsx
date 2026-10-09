import { memo, useEffect, useState } from 'react';
import { downloadJSON, request } from '../data/api';
import './intelligence.css';

type ModelRecord = { model_id: string; version: string; kind: string; status: string; architecture: string; release_scope: string; artifact_sha256: string; provenance: Record<string, unknown>; evaluation_results: Record<string, unknown>; license_information: string };
type Trace = { observation_id?: string; model_version: string; source: string; confidence?: number; freshness_ms?: number | null; differs?: boolean; error?: string; baseline?: { proposed: { requested_phase: string; requested_duration_s: number } }; candidate?: { timestamp_s: number; requested: { requested_phase: string; requested_duration_s: number }; safety_reason: string; accepted: boolean; inference_ms: number; proposed?: { proposed: { requested_phase: string; requested_duration_s: number } }; fallback_reason: string | null; observation: unknown } };
type Run = { unit?: string; model: string; status: string; timesteps: number; seed: number; checkpoints: number; progress: number; loss: Record<string, string>[]; validation: string; error: string | null };
type Snapshot = { readiness: string; overview: { vision: string; baseline: string; baseline_version: string; source: string; runtime_ready: boolean }; registry: { models: ModelRecord[]; selections: Record<string, string | null>; events: unknown[] }; training: { status: string; runs?: Run[]; field_vision?: string }; comparison: { status: string; source?: string; episodes?: number; test_metrics?: Record<string, Record<string, number>>; comparisons?: Record<string, unknown>; limitations?: string }; shadow: { state: string; source?: string; records?: Trace[]; dropped_inputs?: number; failures?: number; drift?: string }; diagnostics: { error: string | null; drift: string; physical_activation: boolean } };
const tabs = ['AI OVERVIEW', 'MODEL TRAINING', 'MODEL REGISTRY', 'AI COMPARISON', 'SHADOW AI', 'AI DIAGNOSTICS'] as const;
const numeric = (value: number | undefined | null) => value == null ? 'NOT MEASURED' : value.toFixed(2);

function LossChart({ rows }: { rows: Record<string, string>[] }) {
  const keys = ['train/loss', 'train/box_loss', 'train_loss'];
  const key = keys.find(k => rows.some(row => row[k]?.trim()));
  const values = key ? rows.map(row => row[key]?.trim() ? Number(row[key]) : NaN).filter(Number.isFinite) : [];
  if (values.length < 2) return <p>Loss graph: NOT MEASURED (fewer than two recorded points)</p>;
  const low = Math.min(...values), high = Math.max(...values);
  const points = values.map((v, i) => `${20 + i * 460 / (values.length - 1)},${100 - (v - low) * 80 / Math.max(0.000001, high - low)}`).join(' ');
  return <figure><figcaption>{key}: recorded minimum {low.toFixed(3)}, maximum {high.toFixed(3)}</figcaption><svg viewBox="0 0 500 120" role="img" aria-label={`${key}, chronological optimizer records`}><path d="M20 20V100H480" fill="none" stroke="#94a3b8"/><polyline points={points} fill="none" stroke="#0891b2" strokeWidth="3"/></svg></figure>;
}

export default memo(function Intelligence() {
  const [tab, setTab] = useState<typeof tabs[number]>('AI OVERVIEW');
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState('');
  const [lanes, setLanes] = useState(2);
  const [length, setLength] = useState(90);
  const [headway, setHeadway] = useState(2);
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [trace, setTrace] = useState<Trace | null>(null);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller = new AbortController();
      try {
        const snapshot = await request<Snapshot>('/api/v1/ai', 'GET', undefined, AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]));
        if (!stopped) { setData(snapshot); setError(''); }
      } catch (e) { if (!stopped) setError(e instanceof Error ? e.message : String(e)); }
      finally { if (!stopped) timer = setTimeout(() => void refresh(), 2500); }
    };
    void refresh();
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); };
  }, []);
  async function action(path: string, body: unknown) {
    setBusy(true);
    try { await request(path, 'POST', body); setData(await request<Snapshot>('/api/v1/ai')); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const approved = data?.registry.models.filter(m => m.status === 'APPROVED' && m.release_scope === 'RESEARCH_SHADOW') ?? [];
  const metrics = data?.comparison.test_metrics;
  const records = data?.shadow.records ?? [];
  const latencies = records.flatMap(r => r.candidate?.proposed ? [r.candidate.inference_ms] : []).sort((a, b) => a - b);
  const percentile = (q: number) => {
    if (!latencies.length) return undefined;
    const i = (latencies.length - 1) * q, low = Math.floor(i), high = Math.ceil(i);
    return latencies[low] + (latencies[high] - latencies[low]) * (i - low);
  };

  return <div className="ai-workspace">
    <nav className="ai-tabs" aria-label="AI sections">{tabs.map(t => <button key={t} className={tab === t ? 'selected' : ''} aria-pressed={tab === t} onClick={() => setTab(t)}>{t}</button>)}</nav>
    {error && <p role="alert" className="error-text">{error}</p>}
    {!data ? <div className="empty">{error ? 'Backend unavailable' : 'Loading measured AI records…'}</div> : <>
      <p className="ai-readiness">{data.readiness}</p>
      {tab === 'AI OVERVIEW' && <section className="card ai-panel"><h2>Verified intelligence</h2><dl className="ai-facts"><dt>Vision currently configured</dt><dd>{data.overview.vision}</dd><dt>Baseline controller</dt><dd>{data.overview.baseline} · {data.overview.baseline_version}</dd><dt>Shadow selection</dt><dd>{data.registry.selections.RESEARCH_SHADOW ?? 'EMPTY'} · {data.shadow.state}</dd><dt>Observation source</dt><dd>{data.overview.source} · {data.overview.runtime_ready ? 'READY' : 'NOT READY'}</dd><dt>Field Vision training</dt><dd>{data.training.field_vision ?? 'NOT TRAINED'}</dd><dt>Latest independent comparison</dt><dd>{data.comparison.status} · {data.comparison.source ?? 'EMPTY'} · {data.comparison.episodes ?? 0} episodes</dd></dl><p className="muted">Research Shadow approval allows observation only. Physical RL activation is blocked.</p></section>}
      {tab === 'MODEL TRAINING' && <section className="card ai-panel"><h2>Actual training runs</h2>{!data.training.runs?.length ? <p>EMPTY / NOT TRAINED</p> : data.training.runs.map(run => <details key={run.model} className="ai-run"><summary>{run.model} · {run.status} · {run.timesteps} {run.unit ?? 'steps'}</summary><progress max={1} value={run.progress}/><p>Seed {run.seed} · {run.checkpoints} checkpoints · {run.validation}</p>{run.error && <p role="alert">{run.error}</p>}{run.loss.length ? <><LossChart rows={run.loss}/><h3>Recorded optimizer measurements</h3><div className="ai-scroll"><table><thead><tr>{Object.keys(run.loss[0]).map(key => <th key={key}>{key}</th>)}</tr></thead><tbody>{run.loss.slice(-30).map((row, i) => <tr key={i}>{Object.entries(row).map(([key, value]) => <td key={key}>{value}</td>)}</tr>)}</tbody></table></div></> : <p>Loss: NOT MEASURED</p>}</details>)}</section>}
      {tab === 'MODEL REGISTRY' && <section className="card ai-panel"><h2>Model Registry</h2>{!data.registry.models.length ? <p>EMPTY</p> : data.registry.models.map(m => <details key={`${m.model_id}@${m.version}`} className="ai-run"><summary>{m.model_id} · {m.version} · {m.kind} · <b>{m.status}</b></summary><p>{m.architecture} · scope: {m.release_scope}</p><p className="ai-hash">SHA256 {m.artifact_sha256}</p><p>{m.license_information}</p><details><summary>Complete metadata and compatibility</summary><pre>{JSON.stringify(m, null, 2)}</pre></details><h3>Provenance</h3><pre>{JSON.stringify(m.provenance, null, 2)}</pre><h3>Release validation</h3><pre>{JSON.stringify(m.evaluation_results, null, 2)}</pre></details>)}<details className="ai-run"><summary>Configuration audit</summary><pre>{JSON.stringify(data.registry.events, null, 2)}</pre></details></section>}
      {tab === 'AI COMPARISON' && <section className="card ai-panel"><h2>{data.comparison.source ?? 'EMPTY'} · independent benchmark</h2><p>{data.comparison.limitations}</p>{!metrics ? <p>EMPTY / NOT EVALUATED</p> : <><div className="ai-bars" aria-label="Measured mean delay per arrival, seconds">{Object.entries(metrics).map(([name, values]) => <div key={name} data-policy={name}><span>{name === 'max_pressure' ? 'Max-Pressure (upstream proxy)' : name}</span><meter min={0} max={Math.max(1, ...Object.values(metrics).map(v => v.average_delay_s)) * 1.1} value={values.average_delay_s}/><b>{numeric(values.average_delay_s)} s</b></div>)}</div><div className="ai-scroll"><table><thead><tr><th>Policy</th><th>Delay s/arrival</th><th>Queue objects</th><th>Departures/scenario</th><th>Fairness gap s</th><th>Starved entities</th><th>Safety violations</th></tr></thead><tbody>{Object.entries(metrics).map(([name, values]) => <tr key={name}><th>{name}</th>{['average_delay_s', 'queue_count_mean', 'throughput_entities', 'fairness_direction_max_gap_s', 'starvation_events', 'safety_violations'].map(key => <td key={key}>{numeric(values[key])}</td>)}</tr>)}</tbody></table></div><details><summary>Paired confidence intervals</summary><pre>{JSON.stringify(data.comparison.comparisons, null, 2)}</pre></details></>}<button className="secondary-button" onClick={() => downloadJSON(data.comparison, 'rohai-ai-comparison.json')}>Export measured report</button></section>}
      {tab === 'SHADOW AI' && <section className="card ai-panel"><h2>Shadow · {data.shadow.state} · {data.shadow.source ?? 'NO SOURCE'}</h2><p>Actual decisions on observed input; counterfactual vehicle delay: NOT MEASURABLE. Simulated outcomes are shown only in AI COMPARISON.</p><form className="ai-shadow-form" onSubmit={e => { e.preventDefault(); void action('/api/v1/ai/shadow', { model_key: model, reason, geometry: { lanes_per_approach: lanes, lane_length_m: length, headway_s: headway } }); }}><label>Approved research model<select required value={model} onChange={e => setModel(e.target.value)}><option value="">Choose model</option>{approved.map(m => <option key={m.model_id} value={`${m.model_id}@${m.version}`}>{m.model_id} · {m.version}</option>)}</select></label><label>Lanes per approach<input type="number" min={1} max={8} value={lanes} onChange={e => setLanes(+e.target.value)} required/></label><label>Lane length, m<input type="number" min={1} max={1000} value={length} onChange={e => setLength(+e.target.value)} required/></label><label>Headway, s<input type="number" min={0.5} max={10} step={0.1} value={headway} onChange={e => setHeadway(+e.target.value)} required/></label><label>Configuration reason<input value={reason} onChange={e => setReason(e.target.value)} maxLength={500} required/></label><label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>Geometry values describe this observed intersection</label><button className="primary-button" disabled={busy || !confirmed || !approved.length}>Start observation only</button></form><div className="ai-actions"><button className="secondary-button" disabled={busy} onClick={() => void action('/api/v1/ai/shadow/stop', {})}>Stop Shadow</button><button className="secondary-button" disabled={busy || !reason.trim()} onClick={() => void action('/api/v1/ai/rollback', { reason })}>Rollback · stop inference</button></div>{!records.length ? <p>EMPTY: no observed Shadow decisions</p> : <div className="ai-scroll"><table><thead><tr><th>Observation / version</th><th>Baseline request</th><th>Candidate request</th><th>Safety</th><th>Confidence / age</th><th>Latency</th><th>Trace</th></tr></thead><tbody>{records.slice(-50).reverse().map((record, i) => <tr key={record.observation_id ?? i}><td>{record.observation_id ?? record.error}<br/>{record.model_version}</td><td>{record.baseline?.proposed.requested_phase} {record.baseline?.proposed.requested_duration_s}s</td><td>{record.candidate?.proposed?.proposed.requested_phase ?? 'UNAVAILABLE'} {record.candidate?.proposed?.proposed.requested_duration_s ?? ''}</td><td>{record.candidate?.safety_reason} · {record.candidate?.fallback_reason ? 'MODEL REJECTED / BASELINE FALLBACK' : record.candidate?.safety_reason === 'duration_clamped' ? 'MODIFIED' : record.candidate?.accepted ? 'ACCEPTED' : 'REJECTED / MODIFIED'}<br/>{record.candidate?.fallback_reason}</td><td>{numeric(record.confidence)} / {numeric(record.freshness_ms)} ms</td><td>{numeric(record.candidate?.inference_ms)} ms</td><td><button onClick={() => setTrace(record)}>View</button></td></tr>)}</tbody></table></div>}{trace && <details open className="ai-trace"><summary>Decision trace · actual recorded input and safety rules</summary><button onClick={() => setTrace(null)}>Close trace</button><pre>{JSON.stringify(trace, null, 2)}</pre></details>}</section>}
      {tab === 'AI DIAGNOSTICS' && <section className="card ai-panel"><h2>Inference and input diagnostics</h2><dl className="ai-facts"><dt>Inference p50 / p95 (retained proposals)</dt><dd>{numeric(percentile(0.5))} / {numeric(percentile(0.95))} ms</dd><dt>Latest input freshness</dt><dd>{numeric(records.at(-1)?.freshness_ms)} ms</dd><dt>Evaluation warning</dt><dd>{data.comparison.limitations ?? 'NOT EVALUATED'}</dd><dt>Worker failures</dt><dd>{data.shadow.failures ?? 0}</dd><dt>Dropped sidecar inputs</dt><dd>{data.shadow.dropped_inputs ?? 0}</dd><dt>Fallback decisions (retained window)</dt><dd>{records.filter(r => r.candidate?.fallback_reason).length}</dd><dt>Data drift</dt><dd>{data.shadow.drift ?? data.diagnostics.drift}</dd><dt>Last model/configuration error</dt><dd>{data.diagnostics.error ?? 'None recorded'}</dd><dt>Physical execution</dt><dd>BLOCKED</dd></dl><p>Vision queue MAE: NOT MEASURED without independent ground truth. Live camera and physical HIL: NOT VERIFIED.</p></section>}
    </>}
  </div>;
});
