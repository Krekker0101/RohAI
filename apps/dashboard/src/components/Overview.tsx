import { useEffect, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowDownRight, ArrowUpRight, ArrowUpLeft, CarFront, Clock3, Crosshair, Expand, Footprints, Layers3, Radio, ShieldCheck, Video, Waypoints, Zap } from 'lucide-react';
import { classNames, clock, directionNames, format, phaseNames, totals, type HistoryPoint, type Snapshot, type SystemInfo } from '../domain/telemetry';
import type { EventItem } from '../data/telemetry-store';
import { Intersection } from './Intersection';
import { VideoFeed } from './VideoFeed';
import { Chart } from './Chart';

const arrows = [ArrowDownRight, ArrowUpLeft, ArrowDownLeft, ArrowUpRight];
const stageNames: Record<string,string> = { green: 'Зелёный', yellow: 'Жёлтый', all_red: 'Все красные' };
const reasonNames: Record<string,string> = { adaptive: 'Длительность рассчитана по текущему спросу', fixed: 'Фиксированная длительность', operator_emergency_priority: 'Приоритет экстренного транспорта', awaiting_observation: 'Ожидание первого наблюдения' };
export function Overview({ snapshot, system, safe, history, events, onEmergency }: { snapshot: Snapshot | null; system: SystemInfo | null; safe: boolean; history: HistoryPoint[]; events: EventItem[]; onEmergency: () => void }) {
  const [view, setView] = useState<'map'|'video'>('map');
  const [metric, setMetric] = useState<'queue'|'wait'|'score'>('queue');
  const panel = useRef<HTMLDivElement>(null);
  const t = totals(snapshot);
  const isVision = snapshot?.mode === 'vision' || snapshot?.mode === 'calibration' || snapshot?.mode === 'demo' || system?.mode === 'vision' || system?.mode === 'calibration' || system?.mode === 'demo';
  useEffect(() => { if (!isVision && view === 'video') setView('map'); }, [isVision, view]);
  const policy = snapshot?.policy ?? system?.policy ?? 'adaptive';
  const hasData = snapshot?.hasTraffic;
  const phase = snapshot?.signals.phase ?? '';
  const stage = safe ? snapshot!.signals.stage : 'unknown';
  const target = stage === 'green' ? snapshot?.target : stage === 'yellow' ? system?.yellow_seconds : system?.all_red_seconds;
  const remaining = safe && target ? Math.max(0, target - snapshot!.signals.elapsed_seconds) : null;
  return <>
    <div className="kpi-grid">
      <Metric icon={<CarFront size={19}/>} label={snapshot?.mode === 'demo' ? 'Объекты Demo AI' : isVision ? 'Транспорт в кадре' : 'Транспорт в очередях'} value={hasData ? format(t.vehicles) : '—'} unit="объектов" foot={snapshot?.mode === 'demo' ? 'Синтетический поток · реальные control/safety' : isVision ? 'Текущие наблюдения · без повторов' : 'Детерминированная симуляция'} />
      <Metric icon={<Layers3 size={19}/>} label="Общая очередь" value={hasData ? format(t.queue) : '—'} unit="объектов" foot="Сумма четырёх направлений" />
      <Metric icon={<Clock3 size={19}/>} label="Среднее ожидание" value={hasData ? format(t.wait,1) : '—'} unit="сек" foot="Взвешено по длине очередей" />
      <Metric icon={<Zap size={19}/>} label="Traffic Score" value={hasData ? format(t.score,1) : '—'} unit="баллов" foot="Спрос для адаптивного контроллера" accent />
    </div>
    <div className="overview-grid">
      <div className="overview-main">
        <section className="card map-card" ref={panel}>
          <div className="card-head"><div><div className="eyebrow">ПЕРЕКРЁСТОК 01</div><h2>Движение в реальном времени <span className={`live-dot ${safe?'':'offline'}`} /></h2></div><div className="map-tools"><div className="segmented"><button className={view==='map'?'selected':''} onClick={()=>setView('map')}><Waypoints size={15}/>Схема</button><button disabled={!isVision} title={!isVision?'Видео доступно в режиме Vision':'Видео с аналитикой'} className={view==='video'?'selected':''} onClick={()=>setView('video')}><Video size={15}/>Камера</button></div><button className="icon-button" aria-label="Развернуть перекрёсток" onClick={()=>{ void panel.current?.requestFullscreen().catch(()=>{}); }}><Expand size={17}/></button></div></div>
          <div className="map-canvas">{view==='video' ? <VideoFeed active={safe}/> : <><Intersection snapshot={snapshot} safe={safe}/><div className="map-tag"><span className="live-dot"/>{system?.mode==='simulation'?'SIMULATION':system?.source_id ?? 'Подключение…'}</div><div className="map-caption"><Crosshair size={13}/>Позиции условные · очереди из телеметрии</div><div className="map-legend"><span><i className="legend-dot green"/>Движение</span><span><i className="legend-dot red"/>Ожидание</span></div></>}</div>
          <div className="map-footer"><span><Radio size={14}/>{isVision?'Vision Engine':'Traffic Simulator'}</span><span>{isVision ? `${format(snapshot?.pipeline?.processing_fps,1)} FPS` : `t = ${clock(snapshot?.time ?? 0)}`}<i/>#{format(snapshot?.sequence)}</span></div>
        </section>
        <section className="card history-card"><div className="card-head"><div><h2>Пульс перекрёстка</h2><p>Последние 90 наблюдений · шаг от 1 секунды</p></div><select aria-label="Метрика графика" value={metric} onChange={e=>setMetric(e.target.value as typeof metric)}><option value="queue">Очередь</option><option value="wait">Ожидание, с</option><option value="score">Traffic Score</option></select></div><Chart history={history} metric={metric}/></section>
      </div>
      <aside className="overview-aside">
        <section className="card controller-card"><div className="card-head"><h2>Управление фазами</h2><ShieldCheck size={18} className="muted"/></div><div className="phase-title"><span className={`badge ${safe?'green':'neutral'}`}>{safe ? 'Контроллер активен' : 'Нет подтверждённых данных'}</span><span className="eyebrow">{policy==='fixed'?'FIXED-TIME':'ADAPTIVE AI'}</span></div><div className="signal-display"><div className="traffic-light">{['red','yellow','green'].map(lamp=><i key={lamp} className={`${lamp} ${(lamp==='red'&&stage==='all_red')||lamp===stage?'on':''}`}/>)}</div><div><div className="signal-direction">{safe ? phaseNames[phase] : 'Сигнал неизвестен'}</div><p>{stageNames[stage] ?? 'Ожидание свежей телеметрии'}</p></div><strong className="countdown">{remaining === null ? '—' : Math.ceil(remaining)}<small>сек</small></strong></div><div className="phase-progress"><span style={{width:`${remaining!==null&&target ? Math.min(100,remaining/target*100):0}%`}}/></div><div className="phase-meta"><span>План зелёного</span><b>{format(snapshot?.target)} с</b></div><div className="signal-grid">{['north','south','east','west','pedestrian'].map(key=><div key={key}><span>{key==='pedestrian'?'Пешеходы':directionNames[key as keyof typeof directionNames]}</span><i className={`lamp ${safe?snapshot?.lamps[key]:'unknown'}`}/></div>)}</div><div className="controller-note"><ShieldCheck size={16}/><span>Команды {system?.hardware==='MockHardwareController'?'Mock-контроллера':'контроллера'}. Физическое подтверждение не подключено.</span></div><button type="button" className="emergency-button" disabled={!safe || system?.mode==='calibration'} onClick={onEmergency}><Zap size={16}/>Экстренный приоритет<ArrowUpRight size={15}/></button></section>
        <section className="insight-card"><div className="insight-icon"><Waypoints size={20}/></div><div className="eyebrow">РЕШЕНИЕ КОНТРОЛЛЕРА</div><h3>{snapshot?.emergency?'Приоритет запрошен':policy==='fixed'?'Работа по расписанию':'Зелёный подстраивается под поток'}</h3><p>{snapshot ? (reasonNames[snapshot.reason] ?? snapshot.reason.replaceAll('_',' ')) : 'Подключаемся к Decision Engine…'}</p><div><span>Входящий поток</span><b>{hasData?format(t.arrival,1):'—'} <small>об./мин</small></b></div></section>
        <section className="card pedestrian-card"><div className="ped-icon"><Footprints size={22}/></div><div><h3>Пешеходы</h3><p>{isVision?'В зонах перехода':'Ожидают перехода'}</p></div><strong>{hasData?format(snapshot.pedestrians):'—'}</strong></section>
      </aside>
    </div>
    <section className="direction-section"><div className="section-heading"><div><h2>Каждое направление — под контролем</h2><p>Очереди, ожидание и состав потока</p></div><span className="subtle-label">4 НАПРАВЛЕНИЯ</span></div><div className="direction-grid">{snapshot?.lanes.map((lane,i)=>{const Icon=arrows[i]; return <article className="card direction-card" key={lane.direction}><div className="direction-head"><div className={`direction-icon dir-${lane.direction}`}><Icon size={21}/></div><h3>{directionNames[lane.direction]}<small>{lane.direction.toUpperCase()} INCOMING</small></h3><span className={`badge ${lane.congestion==='high'?'amber':lane.congestion==='moderate'?'blue':'neutral'}`}>{lane.congestion==='high'?'Высокая':lane.congestion==='moderate'?'Средняя':'Низкая'}</span></div><div className="direction-numbers"><div><strong>{format(lane.queue)}</strong><span>в очереди</span></div><div><strong>{format(lane.wait,1)}<small>с</small></strong><span>ожидание</span></div></div><div className="lane-detail"><span>Макс. ожидание <b>{format(lane.maxWait,1)} с</b></span><span>Поток <b>{format(lane.arrival,1)} /мин</b></span><span>Длина <b>{lane.length===null?'не измеряется':`${format(lane.length,1)} ${lane.lengthUnit}`}</b></span><span>Score <b>{format(lane.score,1)}</b></span>{lane.confidence!==null?<span>Уверенность очереди <b>{format(lane.confidence*100)}%</b></span>:null}</div><div className="class-counts">{lane.counts.map((count,index)=><span key={index} title={classNames[index]}>{classNames[index]}<b>{count}</b></span>)}</div></article>;}) ?? <div className="empty">Ожидаем состояние направлений…</div>}</div></section>
    <section className="card event-card"><div className="card-head"><div><h2>История переключений</h2><p>Наблюдаемые изменения за текущий сеанс</p></div><span className="badge neutral">Safety Controller</span></div><div className="event-list">{events.slice(0,6).map(e=><div key={e.id}><span className={`event-dot ${e.stage}`}/><time>{clock(e.time)}</time><b>{phaseNames[e.phase]}</b><span>{stageNames[e.stage]}</span>{e.emergency?<span className="badge amber">Приоритет</span>:null}</div>)}{!events.length?<p className="empty-small">Событий пока нет</p>:null}</div></section>
  </>;
}
function Metric({ icon, label, value, unit, foot, accent=false }: { icon: React.ReactNode; label: string; value: string; unit: string; foot: string; accent?: boolean }) {
  return <article className={`card metric-card ${accent?'metric-accent':''}`}><div className="metric-label">{label}<span>{icon}</span></div><div className="metric-value">{value}<small>{unit}</small></div><div className="metric-foot">{accent?<span className="live-dot"/>:null}{foot}</div></article>;
}
