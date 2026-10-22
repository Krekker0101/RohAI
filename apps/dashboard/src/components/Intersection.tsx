import { memo, useEffect, useRef, useState } from 'react';
import type { LaneMetrics, Snapshot } from '../domain/telemetry';

type Direction = LaneMetrics['direction'];
type Point = readonly [number, number];
type VehicleKind = 'car' | 'bus' | 'truck' | 'motorcycle' | 'bicycle';
type Vehicle = { id: number; lane: Direction; kind: VehicleKind; born: number; turn: boolean };
type Route = { from: Point; to: Point; control1?: Point; control2?: Point };
const kindByClass: VehicleKind[] = ['car', 'bus', 'truck', 'motorcycle', 'bicycle'];
const palettes = [
  { body: '#315c52', shade: '#23483f', glass: '#b7d8d1', light: '#fff4d5' },
  { body: '#e9eee7', shade: '#cbd8d0', glass: '#789890', light: '#fff6d9' },
  { body: '#557f76', shade: '#3c655d', glass: '#c2ddd7', light: '#f7edcf' },
  { body: '#b8c8c1', shade: '#8ea59d', glass: '#537a73', light: '#fff6d9' },
  { body: '#c69a64', shade: '#98734d', glass: '#d8e5df', light: '#fff1ca' },
  { body: '#7486a1', shade: '#536580', glass: '#c8d9df', light: '#fff6d9' },
];
const approach: Record<Direction, { stop: Point; queue: (i: number) => Point; spawn: Point; angle: number; exit: Point; right: Route }> = {
  north: { stop: [365, 166], queue: i => [365, 145 - i * 42], spawn: [365, 145], angle: 90, exit: [365, 460], right: { from: [365, 145], control1: [365, 185], control2: [330, 212], to: [0, 212] } },
  south: { stop: [435, 294], queue: i => [435, 315 + i * 42], spawn: [435, 315], angle: -90, exit: [435, 0], right: { from: [435, 315], control1: [435, 275], control2: [470, 248], to: [800, 248] } },
  east: { stop: [477, 212], queue: i => [505 + i * 42, 212], spawn: [505, 212], angle: 180, exit: [0, 212], right: { from: [505, 212], control1: [465, 212], control2: [435, 180], to: [435, 0] } },
  west: { stop: [323, 248], queue: i => [295 - i * 42, 248], spawn: [295, 248], angle: 0, exit: [800, 248], right: { from: [295, 248], control1: [335, 248], control2: [365, 280], to: [365, 460] } },
};
const classKind = (lane: LaneMetrics, ordinal: number): VehicleKind => {
  const counts = lane.counts.slice(0, 5), total = counts.reduce((sum, count) => sum + count, 0);
  if (!total) return 'car';
  let slot = Math.abs(ordinal) % total;
  for (let index = 0; index < counts.length; index++) {
    slot -= counts[index];
    if (slot < 0) return kindByClass[index];
  }
  return 'car';
};
const isAllowed = (lane: Direction, phase: string) => phase === 'north_south' ? lane === 'north' || lane === 'south' : phase === 'east_west' ? lane === 'east' || lane === 'west' : false;
const pointOnRoute = (route: Route, p: number): Point => {
  if (!route.control1 || !route.control2) return [route.from[0] + (route.to[0] - route.from[0]) * p, route.from[1] + (route.to[1] - route.from[1]) * p];
  const q = 1 - p;
  return [q ** 3 * route.from[0] + 3 * q ** 2 * p * route.control1[0] + 3 * q * p ** 2 * route.control2[0] + p ** 3 * route.to[0], q ** 3 * route.from[1] + 3 * q ** 2 * p * route.control1[1] + 3 * q * p ** 2 * route.control2[1] + p ** 3 * route.to[1]];
};
const routeAngle = (route: Route, p: number) => {
  const here = pointOnRoute(route, p), next = pointOnRoute(route, Math.min(1, p + 0.01));
  return Math.atan2(next[1] - here[1], next[0] - here[0]) * 180 / Math.PI;
};

function VehicleShape({ kind, id }: { kind: VehicleKind; id: number }) {
  const paint = palettes[Math.abs(id) % palettes.length];
  if (kind === 'bus') return <g>
    <rect x="-22" y="-8.5" width="44" height="17" rx="4.2" fill={paint.shade}/><rect x="-21" y="-8" width="42" height="14.5" rx="3.7" fill={paint.body}/>
    {[-14,-6,2,10].map(x=><rect key={x} x={x} y="-5.8" width="6" height="4.5" rx="1" fill={paint.glass}/>)}<rect x="14" y="-5.8" width="5" height="10.8" rx="1.4" fill={paint.glass}/><path d="M-20 2h39" stroke={paint.shade} strokeWidth=".8"/><circle cx="-19" cy="0" r=".8" fill={paint.light}/><circle cx="20" cy="0" r=".8" fill={paint.light}/>
  </g>;
  if (kind === 'truck') return <g>
    <rect x="-22" y="-7.5" width="28" height="15" rx="2.5" fill={paint.shade}/><rect x="-21" y="-7" width="26" height="13" rx="2" fill={paint.body}/><path d="M6-6h8q2 0 3 2l4 5v5H6z" fill={paint.shade}/><path d="M7-5h6q1 0 2 2l2 3H7z" fill={paint.glass}/><path d="M-20 5h22" stroke="#ffffff70" strokeWidth=".8"/><circle cx="-20" cy="0" r=".8" fill={paint.light}/><circle cx="18" cy="4" r=".8" fill={paint.light}/>
  </g>;
  if (kind === 'motorcycle') return <g><circle cx="-6" cy="0" r="3.2" fill="#263d39"/><circle cx="7" cy="0" r="3.2" fill="#263d39"/><path d="M-6 0l4-4 5 1 4 3H1l-3-3-2 3z" fill={paint.body} stroke={paint.shade} strokeWidth=".8"/><circle cx="7" cy="0" r=".8" fill={paint.light}/></g>;
  if (kind === 'bicycle') return <g><circle cx="-8" cy="0" r="4" fill="none" stroke="#354b47" strokeWidth="1.4"/><circle cx="8" cy="0" r="4" fill="none" stroke="#354b47" strokeWidth="1.4"/><path d="m-8 0 4-6 5 6h-9l3-6h5" fill="none" stroke={paint.body} strokeWidth="1.4" strokeLinejoin="round"/></g>;
  const suv = id % 3 === 0;
  return <g>
    <path d={suv ? 'M-15-5.5Q-14-7.5-10-7.5H9Q14-7.5 15-4L16 0 15 4Q14 7.5 9 7.5H-10Q-14 7.5-15 5.5L-16 0Z' : 'M-15-4.8Q-14-7-9-7H8Q13-7 14.5-4L16 0 14.5 4Q13 7-8 7Q-14 7-15-4.8Z'} fill={paint.shade}/>
    <path d="M-14-4.5Q-13-6.3-9-6.3H8Q12-6.3 13.5-3.8L15 0 13.5 3.8Q12 6.3-8 6.3Q-13 6.3-14 4.5Z" fill={paint.body}/>
    <path d={suv ? 'M-7-5.2H5Q8-5.2 10-3L11.5-1H-8Z' : 'M-8-4.8H4Q7-4.8 9-2.7L10-1H-9Z'} fill={paint.glass}/><path d={suv ? 'M-8 1h19l-1.5 2Q8 5-6 5Z' : 'M-9 1h19L8 3Q6 5-7 5Z'} fill={paint.glass}/>
    <path d="M-1-5v10" stroke={paint.shade} strokeWidth=".8"/><path d="M-15-3h2m-2 6h2m26-6h2m-2 6h2" stroke="#182c29" strokeWidth="2.5" strokeLinecap="round"/><rect x="13.5" y="-2.8" width="1.5" height="1.7" rx=".5" fill={paint.light}/><rect x="13.5" y="1.1" width="1.5" height="1.7" rx=".5" fill={paint.light}/><rect x="-15" y="-2.6" width="1.2" height="1.4" rx=".4" fill="#d96e62"/><rect x="-15" y="1.2" width="1.2" height="1.4" rx=".4" fill="#d96e62"/>
  </g>;
}

function VehicleGlyph({ kind, id, x, y, angle, queued, lane, progress, cameraOverlay = false }: { kind: VehicleKind; id: number; x: number; y: number; angle: number; queued: boolean; lane: Direction; progress?: number; cameraOverlay?: boolean }) {
  const long = kind === 'bus' || kind === 'truck', size = kind === 'bicycle' ? 0.86 : kind === 'motorcycle' ? 0.9 : queued ? 0.92 : 1;
  return <g className={`intersection-car ${queued ? 'queued' : 'moving'}`} data-testid={queued ? 'queued-car' : 'moving-car'} data-lane={lane} data-progress={progress?.toFixed(3)} data-kind={kind} transform={`translate(${x} ${y}) rotate(${angle}) scale(${size})`}>
    <ellipse cx="0" cy="2.2" rx={long ? 20 : 15} ry={long ? 8.2 : 7} fill="#223b3528"/>
    <g><VehicleShape kind={kind} id={id}/></g>
    {cameraOverlay ? <g className="demo-track-overlay" aria-hidden="true"><rect x={long?-24:-19} y={long?-11:-10} width={long?48:38} height={long?22:20} rx="2"/><text x={long?-23:-18} y={long?-13:-12}>#{id} · DEMO</text></g> : null}
  </g>;
}

function Cars({ snapshot, safe, cameraOverlay }: { snapshot: Snapshot | null; safe: boolean; cameraOverlay: boolean }) {
  const [active, setActive] = useState<Vehicle[]>([]);
  const [serial, setSerial] = useState(0);
  const lastDeparture = useRef<Partial<Record<Direction, number>>>({});
  const lastTime = useRef<number | null>(null);
  useEffect(() => {
    if (!snapshot) return;
    const restarted = lastTime.current !== null && snapshot.time < lastTime.current;
    lastTime.current = snapshot.time;
    if (restarted) lastDeparture.current = {};
    const live = (restarted ? [] : active).filter(car => snapshot.time - car.born < (car.turn ? 4.8 : 6.2));
    let nextId = serial;
    const next = [...live];
    if (safe && snapshot.signals.stage === 'green') {
      for (const lane of snapshot.lanes) {
        if (!isAllowed(lane.direction, snapshot.signals.phase)) continue;
        const present = next.filter(car => car.lane === lane.direction).length;
        const previousCar = next.filter(car => car.lane === lane.direction).sort((a, b) => b.born - a.born)[0];
        const headway = previousCar?.kind === 'bus' || previousCar?.kind === 'truck' ? 1.9 : 1.45;
        if (lane.queue <= present || present >= 3 || snapshot.time - (lastDeparture.current[lane.direction] ?? -Infinity) < headway) continue;
        const id = nextId++;
        const kind = classKind(lane, id);
        next.push({ id, lane: lane.direction, kind, born: snapshot.time, turn: id % 4 === 0 });
        lastDeparture.current[lane.direction] = snapshot.time;
      }
    }
    setActive(next);
    if (nextId !== serial) setSerial(nextId);
  // snapshot sequence changes on every published simulation observation.
  }, [snapshot?.sequence, safe]);

  if (!snapshot) return null;
  const waiting = snapshot.lanes.flatMap(lane => {
    const config = approach[lane.direction], movingCount = active.filter(car => car.lane === lane.direction).length;
    const count = Math.max(0, Math.min(7, Math.floor(lane.queue) - movingCount));
    return Array.from({ length: count }, (_, i) => {
      const queueIndex = i + movingCount, [x, y] = config.queue(queueIndex), id = queueIndex + lane.direction.charCodeAt(0) * 17;
      return <VehicleGlyph key={`queue-${lane.direction}-${queueIndex}`} kind={classKind(lane, id)} id={id} lane={lane.direction} x={x} y={y} angle={config.angle} queued cameraOverlay={cameraOverlay} />;
    });
  });
  return <g className="traffic-flow">{waiting}{active.map(car => {
    const age = Math.max(0, snapshot.time - car.born), route = car.turn ? approach[car.lane].right : { from: approach[car.lane].spawn, to: approach[car.lane].exit };
    const duration = car.turn ? 4.8 : 6.2, progress = Math.min(1, age / duration), [x, y] = pointOnRoute(route, progress);
    return <VehicleGlyph key={`moving-${car.id}`} kind={car.kind} id={car.id} lane={car.lane} x={x} y={y} angle={routeAngle(route, progress)} progress={progress} queued={false} cameraOverlay={cameraOverlay}/>;
  })}</g>;
}

const trees = [[66,65],[99,65],[132,65],[66,98],[132,98],[664,66],[697,66],[730,66],[730,99],[68,367],[101,367],[134,367],[68,400],[698,365],[731,365],[664,398],[697,398],[731,398]] as const;
const blocks = [[25,24,274,110],[501,24,274,110],[25,326,274,110],[501,326,274,110]] as const;

export const Intersection = memo(function Intersection({ snapshot, safe, cameraOverlay = false }: { snapshot: Snapshot | null; safe: boolean; cameraOverlay?: boolean }) {
  const green = safe && snapshot?.signals.stage === 'green' ? snapshot.signals.phase : null;
  return <svg className="intersection" viewBox="0 0 800 460" role="img" aria-label="Схема перекрёстка с отдельными полосами, реалистичными автомобилями и сигналами светофора">
    <defs>
      <pattern id="map-grid" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".7" fill="#dce3dc"/></pattern>
      <pattern id="crosswalk" width="13" height="30" patternUnits="userSpaceOnUse"><rect width="7" height="30" fill="#fff"/></pattern>
      <filter id="map-shadow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="3" stdDeviation="3" floodColor="#647365" floodOpacity=".14"/></filter>
    </defs>
    <rect width="800" height="460" fill="#edf1ec"/><rect width="800" height="460" fill="url(#map-grid)"/>
    {blocks.map(([x,y,w,h],i)=><g key={i}><rect x={x} y={y} width={w} height={h} rx="17" fill="#e2e9df" stroke="#d7e0d2"/><rect x={x+136} y={y+19} width="115" height="72" rx="9" fill="#f7f9f4" stroke="#d6dfd0" filter="url(#map-shadow)"/><path d={`M${x+153} ${y+34}h80v42h-80z`} fill="none" stroke="#e5eadf"/><path d={`M${x+193} ${y+34}v42`} stroke="#e5eadf"/></g>)}
    {trees.map(([x,y],i)=><g key={i}><circle cx={x+2} cy={y+3} r="11" fill="#c8d5c3" opacity=".45"/><circle cx={x} cy={y} r="10" fill={i%2?'#c6d7bd':'#bdd0b4'}/><circle cx={x-2} cy={y-3} r="6" fill="#d1dfc9"/></g>)}
    <path d="M325 0h150v155q0 10 10 10h315v130H485q-10 0-10 10v155H325V305q0-10-10-10H0V165h315q10 0 10-10z" fill="#fff"/>
    <path d="M335 0h130v165q0 10 10 10h325v110H475q-10 0-10 10v165H335V295q0-10-10-10H0V175h325q10 0 10-10z" fill="#dce2e2"/>
    <path d="M400 0v148M400 312v148M0 230h310M490 230h310" stroke="#fbfcf9" strokeWidth="3" strokeDasharray="12 11"/>
    <path d="M339 148h53M408 312h53M310 236v43M490 181v43" stroke="#fff" strokeWidth="5"/>
    <rect x="338" y="154" width="124" height="15" fill="url(#crosswalk)"/><rect x="338" y="292" width="124" height="15" fill="url(#crosswalk)"/>
    {Array.from({length:8},(_,i)=><g key={i}><rect x="315" y={178+i*13} width="14" height="7" fill="#fff"/><rect x="471" y={178+i*13} width="14" height="7" fill="#fff"/></g>)}
    <rect x="345" y="182" width="110" height="96" rx="5" fill="none" stroke="#c4ceca" strokeDasharray="4 6"/>
    {green?<g className="flow-path" stroke="#30a578" strokeWidth="3" opacity=".65" fill="none" strokeDasharray="8 13">{green==='north_south'?<><path d="M365 140v180"/><path d="M435 320V140"/></>:green==='east_west'?<><path d="M510 212H290"/><path d="M290 248h220"/></>:null}</g>:null}
    <circle cx="400" cy="230" r="20" fill="#f5f8f3" stroke="#ced9cb"/><path d="M390 236v-12m10 16v-20m10 16v-12" stroke="#63986e" strokeWidth="3" strokeLinecap="round"/>
    <Cars snapshot={snapshot} safe={safe} cameraOverlay={cameraOverlay}/>
    {snapshot?.lanes.map(lane=>{const config=approach[lane.direction],lamp=safe?snapshot.lamps[lane.direction]:'unknown',[x,y]=config.stop,angle=lane.direction==='north'?90:lane.direction==='south'?-90:lane.direction==='east'?180:0;return <g key={lane.direction}><g transform={`translate(${x} ${y}) rotate(${angle})`}><rect x="-12" y="-42" width="7" height="27" rx="3.5" fill="#243a35"/><circle cx="-8.5" cy="-36" r="2" fill={lamp==='red'?'#f67c74':'#60706a'}/><circle cx="-8.5" cy="-28.5" r="2" fill={lamp==='yellow'?'#ffd170':'#60706a'}/><circle cx="-8.5" cy="-21" r="2" fill={lamp==='green'?'#7eedaf':'#60706a'}/></g></g>})}
    <g fill="#8b9b91" fontSize="11" fontFamily="inherit" letterSpacing="2"><text x="190" y="156">WEST</text><text x="555" y="316">EAST</text></g>
    <g transform="translate(745 32)"><circle r="19" fill="#fff" opacity=".9"/><path d="m0-10 5 16-5-3-5 3z" fill="#537367"/><text y="-26" textAnchor="middle" fontSize="10" fill="#5b7469">N</text></g>
  </svg>;
});
