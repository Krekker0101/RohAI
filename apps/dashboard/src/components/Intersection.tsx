import { memo } from 'react';
import type { Snapshot } from '../domain/telemetry';

const trees = [[66,65],[99,65],[132,65],[66,98],[132,98],[664,66],[697,66],[730,66],[730,99],[68,367],[101,367],[134,367],[68,400],[698,365],[731,365],[664,398],[697,398],[731,398]];
const blocks = [[25,24,274,110],[501,24,274,110],[25,326,274,110],[501,326,274,110]];
const lanePositions = { north: [365,130,0], south: [435,330,180], east: [499,201,90], west: [301,261,-90] };
export const Intersection = memo(function Intersection({ snapshot, safe }: { snapshot: Snapshot | null; safe: boolean }) {
  return <svg className="intersection" viewBox="0 0 800 460" role="img" aria-label="Схема перекрёстка. Машины показывают количество в очередях, позиции условные.">
    <defs>
      <pattern id="map-grid" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.7" fill="#dce3dc" /></pattern>
      <pattern id="crosswalk" width="13" height="30" patternUnits="userSpaceOnUse"><rect width="7" height="30" fill="#fff" /></pattern>
      <filter id="map-shadow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="3" stdDeviation="3" floodColor="#647365" floodOpacity=".14" /></filter>
    </defs>
    <rect width="800" height="460" fill="#edf1ec" /><rect width="800" height="460" fill="url(#map-grid)" />
    {blocks.map(([x,y,w,h],i) => <g key={i}><rect x={x} y={y} width={w} height={h} rx="17" fill="#e2e9df" stroke="#d7e0d2" /><rect x={x+136} y={y+19} width="115" height="72" rx="9" fill="#f7f9f4" stroke="#d6dfd0" filter="url(#map-shadow)" /><path d={`M${x+153} ${y+34}h80v42h-80z`} fill="none" stroke="#e5eadf" /><path d={`M${x+193} ${y+34}v42`} stroke="#e5eadf" /></g>)}
    {trees.map(([x,y],i) => <g key={i}><circle cx={x+2} cy={y+3} r="11" fill="#c8d5c3" opacity=".45" /><circle cx={x} cy={y} r="10" fill={i%2?'#c6d7bd':'#bdd0b4'} /><circle cx={x-2} cy={y-3} r="6" fill="#d1dfc9" /></g>)}
    <path d="M325 0h150v155q0 10 10 10h315v130H485q-10 0-10 10v155H325V305q0-10-10-10H0V165h315q10 0 10-10z" fill="#fff" />
    <path d="M335 0h130v165q0 10 10 10h325v110H475q-10 0-10 10v165H335V295q0-10-10-10H0V175h325q10 0 10-10z" fill="#dce2e2" />
    <path d="M400 0v148M400 312v148M0 230h310M490 230h310" stroke="#fbfcf9" strokeWidth="3" strokeDasharray="12 11" />
    <path d="M339 148h53M408 312h53M310 236v43M490 181v43" stroke="#fff" strokeWidth="5" />
    <rect x="338" y="154" width="124" height="15" fill="url(#crosswalk)" /><rect x="338" y="292" width="124" height="15" fill="url(#crosswalk)" />
    <rect x="318" y="178" width="14" height="103" fill="url(#crosswalk)" transform="rotate(90 325 230)" opacity="0" />
    {Array.from({length:8},(_,i)=><g key={i}><rect x="315" y={178+i*13} width="14" height="7" fill="#fff" /><rect x="471" y={178+i*13} width="14" height="7" fill="#fff" /></g>)}
    <rect x="345" y="182" width="110" height="96" rx="5" fill="none" stroke="#c4ceca" strokeDasharray="4 6" />
    {safe && snapshot?.signals.stage === 'green' ? <g className="flow-path" stroke="#30a578" strokeWidth="3" opacity=".65" fill="none" strokeDasharray="8 13">
      {snapshot.signals.phase === 'north_south' ? <><path d="M365 142v180" /><path d="M435 318V139" /></> : snapshot.signals.phase === 'east_west' ? <><path d="M307 258h190" /><path d="M495 202H305" /></> : null}
    </g> : null}
    <circle cx="400" cy="230" r="20" fill="#f5f8f3" stroke="#ced9cb" /><path d="M390 236v-12m10 16v-20m10 16v-12" stroke="#63986e" strokeWidth="3" strokeLinecap="round" />
    {snapshot?.lanes.map(lane => {
      const [x,y,angle] = lanePositions[lane.direction];
      const lamp = safe ? snapshot.lamps[lane.direction] : 'unknown';
      return <g key={lane.direction} transform={`translate(${x} ${y}) rotate(${angle})`}>
        <rect x="-33" y="-21" width="7" height="27" rx="3.5" fill="#243a35" />
        <circle cx="-29.5" cy="-15" r="2" fill={lamp === 'red' ? '#f67c74' : '#60706a'} />
        <circle cx="-29.5" cy="-7.5" r="2" fill={lamp === 'yellow' ? '#ffd170' : '#60706a'} />
        <circle cx="-29.5" cy="0" r="2" fill={lamp === 'green' ? '#7eedaf' : '#60706a'} />
        {Array.from({length: Math.min(lane.queue, 8)}, (_,i) => <g key={i} transform={`translate(${i%2*23-10} ${-Math.floor(i/2)*29-16})`} filter="url(#map-shadow)">
          <rect x="-7" y="-12" width="14" height="24" rx="4" fill={['#426f65','#99b3af','#fff','#829499'][i%4]} stroke="#718e88" strokeWidth=".6" />
          <rect x="-5" y="2" width="10" height="4" rx="1.5" fill="#cbdcd9" /><rect x="-5" y="-8" width="10" height="3" rx="1" fill="#cbdcd9" />
          <path d="M-5 10h3m4 0h3" stroke="#fff3cf" strokeWidth="1.7" />
        </g>)}
      </g>;
    })}
    <g fill="#8b9b91" fontSize="11" fontFamily="inherit" letterSpacing="2"><text x="190" y="156">WEST</text><text x="555" y="316">EAST</text></g>
    <g transform="translate(745 32)"><circle r="19" fill="#fff" opacity=".9" /><path d="m0-10 5 16-5-3-5 3z" fill="#537367" /><text y="-26" textAnchor="middle" fontSize="10" fill="#5b7469">N</text></g>
  </svg>;
});
