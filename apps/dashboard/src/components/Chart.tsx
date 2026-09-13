import { useId } from 'react';
import { clock, format, type HistoryPoint } from '../domain/telemetry';

export function Chart({ history, metric = 'queue' }: { history: HistoryPoint[]; metric?: 'queue' | 'wait' | 'score' }) {
  const id = useId();
  if (history.length < 2) return <div className="chart-empty">Накапливаем историю текущего сеанса…</div>;
  const max = Math.max(5, ...history.map(p => p[metric]));
  const width = 680, height = 124;
  const points = history.map((p,i) => `${i/(history.length-1)*width},${height-p[metric]/max*(height-15)}`);
  return <div className="chart" role="img" aria-label={`История: ${metric === 'queue' ? 'очередь' : metric === 'wait' ? 'ожидание' : 'Traffic Score'}, последнее значение ${format(history.at(-1)?.[metric],1)}`}>
    <div className="chart-y"><span>{format(max,0)}</span><span>{format(max/2,0)}</span><span>0</span></div>
    <svg viewBox={`0 0 ${width} ${height+5}`} preserveAspectRatio="none">
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop stopColor="#279976" stopOpacity=".16" /><stop offset="1" stopColor="#279976" stopOpacity=".01" /></linearGradient></defs>
      {[15, height/2, height].map(y => <path key={y} d={`M0 ${y}H${width}`} stroke="#e8edeb" strokeDasharray="3 5" />)}
      <path d={`M0 ${height}L${points.join('L')}L${width} ${height}Z`} fill={`url(#${id})`} />
      <polyline points={points.join(' ')} fill="none" stroke="#248965" strokeWidth="2.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
    <div className="chart-x"><span>{clock(history[0].time)}</span><span>время источника</span><span>{clock(history.at(-1)!.time)}</span></div>
  </div>;
}
