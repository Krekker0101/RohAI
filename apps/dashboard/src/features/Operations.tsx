import { useEffect, useState } from 'react';
import { Activity, Download, ShieldCheck } from 'lucide-react';
import { downloadJSON, request } from '../data/api';
import { getDataSourceMode } from '../data/source';

interface DomainReport {
  domain: string; samples: number; mean_queue_objects: number | null;
  peak_queue_objects: number | null; mean_observed_wait_seconds: number | null;
  mean_fps: number | null; ready_samples: number; measured_queue_samples: number;
}
interface Report {
  since: string; until: string; domains: DomainReport[];
  alerts: { domain: string; reason: string; samples: number }[]; methodology: string;
}
const display = (value: number | null) => value === null ? 'Нет измерений' : value.toFixed(1);

export default function Operations() {
  const [days, setDays] = useState(1);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const isDemo = getDataSourceMode() === 'demo';
  useEffect(() => {
    if (isDemo) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setReport(null);
    request<Report>(`/api/v1/operations/report?days=${days}`, 'GET', undefined,
      AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]))
      .then(data => { if (!controller.signal.aborted) setReport(data); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Ошибка отчёта');
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [days, isDemo, revision]);
  return <div className="feature-page"><section className="card">
    <div className="card-head"><div><h2>Отчёт наблюдений</h2><p>Очереди, ожидание и доступность измерений за период</p></div><Activity size={20}/></div>
    <div className="table-toolbar">
      <label>Период <select aria-label="Период отчёта" value={days} onChange={e => setDays(Number(e.target.value))}>
        <option value={1}>24 часа</option><option value={7}>7 дней</option><option value={30}>30 дней</option>
      </select></label>
      <button className="secondary-button" disabled={isDemo || loading} onClick={() => setRevision(v => v + 1)}>Обновить</button>
      <button className="secondary-button" disabled={isDemo || !report || loading} onClick={() => downloadJSON(report, `rohai-report-${days}d.json`)}><Download size={16}/>Экспорт отчёта</button>
    </div>
    {isDemo ? <div className="empty-small">Отчёты доступны при подключении к реальному backend. Выберите REAL в настройках подключения.</div> : null}
    {loading ? <div className="empty-small" role="status">Загружаем отчёт…</div> : null}
    {error ? <div className="empty-small" role="alert">{error}<p>Проверьте подключение, токен оператора и включение аналитики на backend.</p></div> : null}
    {report && !isDemo ? <><p className="padded">{new Date(report.since).toLocaleString()} — {new Date(report.until).toLocaleString()}</p>
      <div className="table-scroll"><table><thead><tr><th>Источник</th><th>Измерений</th><th>Очередь, среднее</th><th>Очередь, максимум</th><th>Ожидание, с</th><th>FPS</th><th>Готовых наблюдений</th></tr></thead>
      <tbody>{report.domains.map(domain => <tr key={domain.domain}><td>{domain.domain}</td><td>{domain.samples}</td><td>{display(domain.mean_queue_objects)}</td><td>{display(domain.peak_queue_objects)}</td><td>{display(domain.mean_observed_wait_seconds)}</td><td>{display(domain.mean_fps)}</td><td>{domain.ready_samples}/{domain.samples}</td></tr>)}</tbody></table></div>
      {!report.domains.length ? <div className="empty-small">За выбранный период наблюдений пока нет.</div> : null}
      <p className="table-note">Средние рассчитаны по сохранённым наблюдениям. Ожидание в Vision относится к текущей очереди; в Simulation — к прибывшим объектам. Источники учитываются отдельно. Пропуски не считаются нулевыми очередями.</p>
      {report.alerts.length ? <div className="padded"><h3>Проблемы наблюдений</h3>{report.alerts.map(alert => <p key={`${alert.domain}:${alert.reason}`}>{alert.domain}: {alert.reason} — {alert.samples} наблюдений</p>)}</div> : null}
    </> : null}
    <p className="table-note"><ShieldCheck size={14}/> Отчёт описывает наблюдения. Сокращение пробок требует отдельной проверки.</p>
  </section></div>;
}
