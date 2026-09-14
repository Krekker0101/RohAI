import { useEffect, useRef, useState } from 'react';
import { Download, Save, ScanLine, Upload } from 'lucide-react';
import { downloadJSON, request } from '../data/api';
import type { SystemInfo } from '../domain/telemetry';

interface Point { x: number; y: number }
interface Polygon { points: Point[] }
interface Geometry {
  source_id: string;
  detection_zone: Polygon;
  approaches: {
    direction: string;
    enabled?: boolean;
    incoming: Polygon;
    queue_zone: Polygon;
    pedestrian_crossing?: Polygon | null;
    stop_line: { start: Point; end: Point; upstream: Point };
  }[];
}

const expectedDirections = new Set(['north', 'south', 'east', 'west']);
const EPSILON = 1e-9;
const pointValid = (p: Point | undefined) => Boolean(p && Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1);
const cross = (a: Point, b: Point, p: Point) => (b.x-a.x)*(p.y-a.y)-(b.y-a.y)*(p.x-a.x);
const onSegment = (a: Point,b: Point,p: Point) => Math.abs(cross(a,b,p))<=EPSILON && p.x>=Math.min(a.x,b.x)-EPSILON && p.x<=Math.max(a.x,b.x)+EPSILON && p.y>=Math.min(a.y,b.y)-EPSILON && p.y<=Math.max(a.y,b.y)+EPSILON;
const intersects = (a: Point,b: Point,c: Point,d: Point) => onSegment(a,b,c)||onSegment(a,b,d)||onSegment(c,d,a)||onSegment(c,d,b)||(cross(a,b,c)*cross(a,b,d)<0&&cross(c,d,a)*cross(c,d,b)<0);
function polygonValid(polygon: Polygon | null | undefined): boolean {
  if (!Array.isArray(polygon?.points) || polygon.points.length < 3 || polygon.points.length > 64 || !polygon.points.every(pointValid)) return false;
  const points=polygon.points;
  if (new Set(points.map(p=>`${p.x}:${p.y}`)).size !== points.length) return false;
  let area=0;
  for (let i=0;i<points.length;i++) { const a=points[i],b=points[(i+1)%points.length]; area += a.x*b.y-b.x*a.y; }
  if (Math.abs(area)<=EPSILON) return false;
  for (let i=0;i<points.length;i++) {
    const a=points[i],b=points[(i+1)%points.length];
    for (let j=i+2;j<points.length;j++) {
      if (i===0&&j===points.length-1) continue;
      const c=points[j],d=points[(j+1)%points.length];
      if (intersects(a,b,c,d)) return false;
    }
  }
  return true;
}

function preview(text: string): Geometry | null {
  try {
    const g = JSON.parse(text) as Geometry;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(g.source_id) || !polygonValid(g.detection_zone) || !Array.isArray(g.approaches) || g.approaches.length !== 4) return null;
    const directions = new Set(g.approaches.map(a => a.direction));
    if (directions.size !== 4 || [...expectedDirections].some(direction => !directions.has(direction))) return null;
    if (!g.approaches.every(a =>
      polygonValid(a.incoming)
      && polygonValid(a.queue_zone)
      && (a.pedestrian_crossing == null || polygonValid(a.pedestrian_crossing))
      && pointValid(a.stop_line?.start)
      && pointValid(a.stop_line?.end)
      && pointValid(a.stop_line?.upstream)
      && Math.abs(cross(a.stop_line.start,a.stop_line.end,a.stop_line.upstream)) > EPSILON
    )) return null;
    return g;
  } catch {
    return null;
  }
}

export default function Calibration({ system }: { system: SystemInfo|null }) {
  const initialSource = system?.demo || system?.source_id === 'simulation' ? 'demo' : system?.source_id ?? 'demo';
  const [source,setSource]=useState(initialSource);
  const sourceEdited = useRef(false);
  const [text,setText]=useState('');
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const geometry=preview(text);

  useEffect(() => {
    if (sourceEdited.current || !system?.source_id) return;
    setSource(system.demo || system.source_id === 'simulation' ? 'demo' : system.source_id);
  }, [system?.source_id]);

  async function load() {
    setBusy(true); setError(''); setMessage('');
    try {
      const loaded = await request<unknown>(`/api/v1/vision/geometry/${encodeURIComponent(source)}`);
      setText(JSON.stringify(loaded,null,2));
    } catch(e) {
      setError(e instanceof Error?e.message:String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!geometry) { setError('Сначала исправьте JSON конфигурации.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      await request(`/api/v1/vision/geometry/${encodeURIComponent(source)}`,'PUT',geometry);
      setMessage('Геометрия сохранена. Применится после перезапуска Vision pipeline.');
    } catch(e) {
      setError(e instanceof Error?e.message:String(e));
    } finally {
      setBusy(false);
    }
  }

  const points=(p:Polygon)=>p.points.map(v=>`${v.x*800},${v.y*450}`).join(' ');
  return <div className="feature-page"><div className="calibration-notice"><ScanLine size={22}/><div><b>Геометрия конкретной камеры</b><p>Нормализованные координаты 0…1. Для изменения активной камеры запустите backend в режиме calibration: светофоры останутся красными.</p></div></div><section className="card"><div className="card-head calibration-head"><label>Camera ID<input value={source} maxLength={64} pattern="[A-Za-z0-9_-]+" onChange={e=>{sourceEdited.current=true;setSource(e.target.value);}} aria-label="Camera ID"/></label><button type="button" className="secondary-button" disabled={busy||!source} onClick={()=>void load()}><Upload size={16}/>Загрузить ROI</button><button type="button" className="secondary-button" disabled={!geometry} onClick={()=>geometry&&downloadJSON(geometry,`${geometry.source_id}-geometry.json`)}><Download size={16}/>JSON</button><button type="button" className="primary-button" disabled={busy||!geometry||geometry.source_id!==source||(system?.mode==='vision'&&source===system.source_id)} onClick={()=>void save()}><Save size={16}/>Сохранить</button></div><div className="geometry-layout"><div><div className="geometry-preview">{geometry?<svg viewBox="0 0 800 450" role="img" aria-label="Предпросмотр геометрии камеры"><rect width="800" height="450" fill="#edf2ef"/><polygon points={points(geometry.detection_zone)} fill="none" stroke="#7e8d88" strokeDasharray="8 7" strokeWidth="2"/>{geometry.approaches.filter(a=>a.enabled!==false).map((a,i)=><g key={a.direction}><polygon points={points(a.incoming)} fill={['#e0eeeb','#e6e5f4','#e8eddb','#f1e6d8'][i]} stroke={['#3d9981','#8980bc','#94a06e','#c19b6d'][i]} strokeWidth="2"/><polygon points={points(a.queue_zone)} fill="#f3be6640" stroke="#caa059" strokeWidth="2" strokeDasharray="5 5"/>{a.pedestrian_crossing?<polygon points={points(a.pedestrian_crossing)} fill="#95b7d640" stroke="#6d9bc1"/>:null}<line x1={a.stop_line.start.x*800} y1={a.stop_line.start.y*450} x2={a.stop_line.end.x*800} y2={a.stop_line.end.y*450} stroke="#e76c65" strokeWidth="5"/><text x={a.incoming.points[0].x*800+8} y={a.incoming.points[0].y*450+20} fill="#415a50" fontSize="13">{a.direction.toUpperCase()}</text></g>)}</svg>:<div className="empty"><ScanLine size={38}/><h3>Загрузите конфигурацию камеры</h3><p>demo · intel-car · intel-mixed</p></div>}</div><div className="geometry-legend"><span><i className="legend-dot green"/>Incoming ROI</span><span><i className="legend-dot amber"/>Queue zone</span><span><i className="legend-dot red"/>Stop line</span></div><p className="padded muted small">Редактируйте вершины в JSON. Backend проверит пересечения полигонов, границы и четыре направления. Предпросмотр показывает геометрию без видеоподложки.</p></div><label className="geometry-editor">Конфигурация JSON<textarea value={text} onChange={e=>{setText(e.target.value);setMessage('');}} spellCheck={false} placeholder="Загрузите существующую геометрию или вставьте JSON"/>{text&&!geometry?<span className="error-text">JSON не подходит для предпросмотра. Проверьте структуру, четыре направления, stop line и координаты 0…1.</span>:null}</label></div>{error?<p className="error-text padded" role="alert">{error}</p>:null}{message?<p className="success-text padded" role="status">{message}</p>:null}</section></div>;
}
