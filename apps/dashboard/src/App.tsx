import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from 'react';
import {
  Activity,
  ArrowUpRight,
  ChevronRight,
  CircleHelp,
  Download,
  Gauge,
  GitCompareArrows,
  LayoutDashboard,
  Radio,
  ScanLine,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Unplug,
  Waypoints,
  Zap,
} from 'lucide-react';
import { Dialog } from './components/Dialog';
import { Overview } from './components/Overview';
import { downloadJSON, request, setOperatorToken } from './data/api';
import { getBackendBaseUrl, resetBackendBaseUrl, setBackendBaseUrl } from './data/backend';
import { getDataSourceMode, setDataSourceMode, type DataSourceMode } from './data/source';
import { telemetryStore } from './data/telemetry-store';
import { directionNames, phaseNames, type Direction, type Policy } from './domain/telemetry';

const Comparison = lazy(() => import('./features/Comparison'));
const Analytics = lazy(() => import('./features/Analytics'));
const Calibration = lazy(() => import('./features/Calibration'));

const pages = [
  { id: 'overview', label: 'Обзор перекрёстка', short: 'Обзор', icon: LayoutDashboard, title: 'Город в движении.', subtitle: 'Видеть поток. Понимать спрос. Управлять умнее.' },
  { id: 'analytics', label: 'Видеоаналитика', short: 'Аналитика', icon: ScanLine, title: 'Каждый объект на виду.', subtitle: 'Треки, классы транспорта и производительность Vision Engine.' },
  { id: 'comparison', label: 'AI vs Fixed-Time', short: 'Сравнение', icon: GitCompareArrows, title: 'Эффективность, измеренная.', subtitle: 'Проверяйте решения на воспроизводимых сценариях.' },
  { id: 'calibration', label: 'Калибровка ROI', short: 'Калибровка', icon: SlidersHorizontal, title: 'Точность начинается с геометрии.', subtitle: 'Зоны детекции, очереди и стоп-линии вашей камеры.' },
];

export function App() {
  const state = useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);
  const [page, setPage] = useState(() => pages.some(p => p.id === location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
  const [dialog, setDialog] = useState<'settings' | 'emergency' | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [direction, setDirection] = useState<Direction>('north');
  const [ttl, setTtl] = useState(30);
  const [token, setToken] = useState('');
  const [recipe, setRecipe] = useState('demo');
  const [backend, setBackend] = useState(getBackendBaseUrl);
  const [sourceMode, setSourceModeState] = useState<DataSourceMode>(getDataSourceMode);

  useEffect(() => {
    telemetryStore.start();
    const onHash = () => setPage(pages.some(p => p.id === location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
    window.addEventListener('hashchange', onHash);
    return () => {
      telemetryStore.stop();
      window.removeEventListener('hashchange', onHash);
    };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  const current = pages.find(p => p.id === page)!;
  const { snapshot, system, safe } = state;
  const policy = snapshot?.policy ?? system?.policy ?? 'adaptive';
  const isDemo = sourceMode === 'demo';

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError('');
    try {
      await action();
      setNotice(message);
      await telemetryStore.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function selectPolicy(value: Policy) {
    void act(
      () => request('/api/v1/control/policy', 'PUT', { policy: value }),
      `${isDemo ? 'Demo' : 'Real'}: политика принята. Safety Controller управляет переходом фаз.`,
    );
  }

  function switchSource(value: DataSourceMode) {
    if (value === sourceMode) return;
    setDataSourceMode(value);
    setSourceModeState(value);
    telemetryStore.restart();
    setError('');
    setNotice(value === 'demo'
      ? 'Включён презентационный DEMO: синтетический поток, настоящая control/safety логика.'
      : 'Включён REAL: панель читает реальные endpoints выбранного backend.');
  }

  function applyBackend() {
    setError('');
    try {
      const normalized = setBackendBaseUrl(backend);
      setBackend(normalized);
      telemetryStore.restart();
      setNotice(`Backend переключён: ${normalized}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function resetBackend() {
    const normalized = resetBackendBaseUrl();
    setBackend(normalized);
    telemetryStore.restart();
    setNotice(`Backend сброшен: ${normalized}`);
  }

  const command = recipe === 'demo'
    ? '$env:STA_MODE="simulation"'
    : recipe === 'simulation'
      ? '$env:STA_MODE="simulation"'
      : recipe === 'calibration'
        ? '$env:STA_MODE="calibration"; $env:STA_VISION__SOURCE="synthetic"; $env:STA_VISION__SOURCE_ID="demo"; $env:STA_VISION__GEOMETRY_PATH="configs/cameras/demo.json"'
        : '$env:STA_MODE="vision"; $env:STA_VISION__SOURCE="file"; $env:STA_VISION__SOURCE_ID="intel-car"; $env:STA_VISION__URI="recordings/car-detection.mp4"; $env:STA_VISION__GEOMETRY_PATH="configs/cameras/intel-car.json"';
  const launchCommand = `${command}\nuv run uvicorn smart_traffic_backend.main:app --host 127.0.0.1 --port 8000 --workers 1`;

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="Smart Traffic AI — главная"><div className="brand-icon"><Waypoints size={25}/></div><div>smart traffic<span>INTELLIGENCE IN MOTION</span></div></a>
      <div className="workspace-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav>{pages.map(p => <a key={p.id} href={`#${p.id}`} className={page === p.id ? 'active' : ''} aria-current={page === p.id ? 'page' : undefined}><p.icon size={19}/><span>{p.label}</span>{page === p.id ? <i/> : null}</a>)}</nav>
      <div className="sidebar-divider"/>
      <button className="sidebar-button" onClick={() => setDialog('settings')}><Settings2 size={19}/>Подключение</button>
      <div className="sidebar-bottom">
        <div className={`demo-label ${isDemo ? '' : 'real-label'}`}><span className="live-dot"/>{isDemo ? 'DEMO WORKSPACE' : 'REAL WORKSPACE'}</div>
        <div className="hardware-summary"><div className="hardware-icon"><ShieldCheck size={20}/></div><div><b>{system?.hardware === 'MockHardwareController' ? 'Mock Hardware' : system?.hardware ?? 'Подключение'}</b><span>{isDemo ? 'Изолированный показ' : 'Команды контроллера'}</span></div></div>
        <button className="help-button" onClick={() => setDialog('settings')}><CircleHelp size={17}/>Как показать проект<ArrowUpRight size={15}/></button>
        <div className="sidebar-version">SMART TRAFFIC AI <span>v0.1 · demo-ready</span></div>
      </div>
    </aside>

    <div className="app-main">
      <header className="topbar">
        <div className="breadcrumbs">Центр управления<ChevronRight size={13}/><b>{current.short}</b></div>
        <div className="topbar-right">
          <div className="segmented source-switch" aria-label="Источник данных">
            <button className={isDemo ? 'selected demo-selected' : ''} onClick={() => switchSource('demo')}><Sparkles size={13}/>DEMO</button>
            <button className={!isDemo ? 'selected real-selected' : ''} onClick={() => switchSource('real')}><Radio size={13}/>REAL</button>
          </div>
          <span className={`connection-status ${safe ? '' : 'disconnected'}`}><span className="live-dot"/>{safe ? (isDemo ? 'Demo готов' : 'Система онлайн') : state.connected ? 'Ожидание данных' : 'Нет соединения'}</span>
          <span className="topbar-separator"/>
          <button className="icon-button" aria-label="Настройки подключения" onClick={() => setDialog('settings')}><Settings2 size={18}/></button>
          <div className="avatar" title="Локальная панель оператора">OP</div>
        </div>
      </header>

      <main>
        <div className="page-header"><div><div className="eyebrow"><span className="tiny-line"/> SMART TRAFFIC AI / CONTROL CENTER</div><h1>{current.title}</h1><p>{current.subtitle}</p></div><button className="secondary-button export-button" disabled={!snapshot} onClick={() => downloadJSON(snapshot, `traffic-${snapshot?.sequence}.json`)}><Download size={16}/>Экспорт данных</button></div>

        {isDemo ? <div className="demo-mode-banner" role="status"><div className="demo-mode-icon"><Sparkles size={18}/></div><div><b>Презентационный DEMO включён</b><span>Поток и треки синтетические. Decision Engine и Safety Controller — те же, что используются в REAL режиме.</span></div><button onClick={() => switchSource('real')}>Перейти к REAL<ChevronRight size={14}/></button></div> : null}

        <div className="workspace-toolbar">
          <div className="intersection-select"><div className="location-icon"><Waypoints size={18}/></div><div><b>Перекрёсток 01</b><span>{system?.source_id ?? 'Подключение к источнику'}</span></div><span className={`badge ${isDemo ? 'green' : 'neutral'}`}>{isDemo ? 'Demo AI' : system?.mode === 'simulation' ? 'Simulation' : system?.mode === 'calibration' ? 'Calibration' : system?.mode === 'vision' ? 'Vision' : '—'}</span></div>
          <div className="policy-control"><span>Алгоритм</span><div className="segmented policy"><button className={policy === 'fixed' ? 'selected' : ''} disabled={busy || !safe || system?.mode === 'calibration'} onClick={() => selectPolicy('fixed')}><Gauge size={14}/>Fixed-Time</button><button className={policy === 'adaptive' ? 'selected ai-selected' : ''} disabled={busy || !safe || system?.mode === 'calibration'} onClick={() => selectPolicy('adaptive')}><Sparkles size={14}/>Smart AI</button></div></div>
        </div>

        {!safe ? <div className="status-banner" role="status"><Unplug size={18}/><span>{snapshot?.failure ? `Контроллер остановлен: ${snapshot.failure}. Перезапустите источник.` : state.error ?? 'Ожидаем свежие данные backend. Сигналы скрыты до подтверждения связи и готовности.'}</span><button onClick={() => setDialog('settings')}>Подключение<ChevronRight size={14}/></button></div> : null}
        {snapshot?.emergency ? <div className="priority-banner"><Zap size={17}/><span>Экстренный приоритет: {phaseNames[snapshot.emergency]}. Переход выполняет Safety Controller.</span><button disabled={busy} onClick={() => void act(() => request('/api/v1/emergency', 'DELETE'), 'Приоритет отменён')}>Отменить</button></div> : null}
        {error ? <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>Закрыть</button></div> : null}

        <Suspense fallback={<div className="empty">Загружаем раздел…</div>}>
          {page === 'overview' ? <Overview snapshot={snapshot} system={system} safe={safe} history={state.history} events={state.events} onEmergency={() => setDialog('emergency')}/> : page === 'comparison' ? <Comparison/> : page === 'analytics' ? <Analytics snapshot={snapshot}/> : <Calibration system={system}/>} 
        </Suspense>
        <footer className="page-footer"><span><ShieldCheck size={14}/>Vision наблюдает. Decision решает. Safety разрешает.</span><span>Smart Traffic AI · {isDemo ? 'presentation demo' : 'real backend'}</span></footer>
      </main>
    </div>

    {notice ? <div className="toast" role="status"><ShieldCheck size={19}/>{notice}</div> : null}

    {dialog === 'emergency' ? <Dialog title="Экстренный приоритет" onClose={() => setDialog(null)}><div className="dialog-content"><div className="dialog-callout"><Zap size={22}/><p>Запросите приоритет для направления. Жёлтый и защитный интервал сохраняются; мгновенного включения зелёного не будет.</p></div><form onSubmit={e => { e.preventDefault(); void act(() => request('/api/v1/emergency', 'POST', { direction, ttl_seconds: ttl }), 'Запрос приоритета принят'); }}><label>Направление<select value={direction} onChange={e => setDirection(e.target.value as Direction)}>{Object.entries(directionNames).map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label><label>Действие запроса, секунд<input type="number" min="1" max="120" required value={ttl} onChange={e => setTtl(+e.target.value)}/></label><button type="submit" className="primary-button full" disabled={busy || !safe || system?.mode === 'calibration'}><Zap size={16}/>{busy ? 'Отправляем…' : 'Запросить приоритет'}</button></form><button type="button" className="text-button full" disabled={busy || !snapshot?.emergency} onClick={() => void act(() => request('/api/v1/emergency', 'DELETE'), 'Приоритет отменён')}>Отменить активный приоритет</button>{error ? <p className="error-text" role="alert">{error}</p> : null}{notice ? <p className="success-text" role="status">{notice}</p> : null}</div></Dialog> : null}

    {dialog === 'settings' ? <Dialog title="Подключение и демонстрация" onClose={() => setDialog(null)}><div className="dialog-content">
      <div className="source-choice"><button className={isDemo ? 'active' : ''} onClick={() => switchSource('demo')}><Sparkles size={20}/><span><b>DEMO</b><small>Готовый показ без камеры</small></span><i>Синтетический поток + реальные Decision/Safety</i></button><button className={!isDemo ? 'active real' : ''} onClick={() => switchSource('real')}><Radio size={20}/><span><b>REAL</b><small>Настоящие данные backend</small></span><i>YOLO / webcam / video / RTSP / simulation</i></button></div>
      <div className="connection-detail"><Radio size={20}/><div><b>{getBackendBaseUrl()}</b><p>REST + WebSocket · выбранный backend</p></div><span className={`badge ${safe ? 'green' : 'neutral'}`}>{safe ? 'Подключено' : state.connected ? 'WebSocket есть' : 'Ожидание'}</span></div>
      <label>Backend URL<input type="url" inputMode="url" value={backend} onChange={e => setBackend(e.target.value)} placeholder="http://127.0.0.1:8000"/></label>
      <div className="backend-actions"><button type="button" className="secondary-button full" onClick={applyBackend}>Применить backend</button><button type="button" className="text-button" onClick={resetBackend}>Сбросить</button></div>
      <label>Токен оператора {system?.operator_auth_required ? '· обязателен для REAL' : '· сейчас не требуется'}<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} placeholder="X-Operator-Token"/></label>
      <button type="button" className="secondary-button full" onClick={() => { setOperatorToken(token); setNotice('Токен сохранён только в памяти этой вкладки'); }}>Применить токен</button>
      <div className="form-divider"/>
      <h3>Запуск backend</h3>
      <p className="muted small">DEMO всегда доступен внутри запущенного backend. Для REAL выберите нужный реальный источник перед запуском.</p>
      <label>Сценарий запуска<select value={recipe} onChange={e => setRecipe(e.target.value)}><option value="demo">Presentation Demo · готово сразу</option><option value="simulation">REAL channel · simulation</option><option value="vision">REAL channel · YOLO video</option><option value="calibration">Calibration · настройка ROI</option></select></label>
      <pre className="command">{launchCommand}</pre>
      <button className="text-button" onClick={() => void act(() => navigator.clipboard.writeText(launchCommand), 'Команда скопирована')}>Скопировать команду</button>
      <div className="demo-guide"><Activity size={18}/><p><b>Сценарий показа:</b> DEMO → Обзор → «Камера» → Видеоаналитика → AI vs Fixed-Time → Экстренный приоритет. Затем переключите REAL, чтобы показать, что тот же UI работает с настоящим backend.</p></div>
    </div></Dialog> : null}
  </div>;
}
