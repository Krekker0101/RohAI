import { memo, useId } from 'react';
import { stateLabels, type AnimationState } from './state';

export const Avatar = memo(function Avatar({ state, compact = false }: { state: AnimationState; compact?: boolean }) {
  const id = useId().replaceAll(':', '');
  return <div className={`pilot-avatar ${compact ? 'compact' : 'portrait'}`} data-state={state} role="img" aria-label={`Пилот: ${stateLabels[state]}`}>
    <svg className="pilot-portrait" viewBox="0 0 220 240" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={`${id}-suit`} x1="0" x2="1" y1="0" y2="1"><stop stopColor="#244b58"/><stop offset="1" stopColor="#102a35"/></linearGradient>
        <linearGradient id={`${id}-face`} x1="0" x2="1" y1="0" y2="1"><stop stopColor="#f1c5a3"/><stop offset="1" stopColor="#d69a78"/></linearGradient>
        <radialGradient id={`${id}-glow`}><stop stopColor="#58d8ba" stopOpacity=".28"/><stop offset="1" stopColor="#58d8ba" stopOpacity="0"/></radialGradient>
      </defs>
      <circle className="portrait-glow" cx="110" cy="108" r="98" fill={`url(#${id}-glow)`}/>
      <circle className="portrait-ring portrait-ring-outer" cx="110" cy="108" r="86"/>
      <circle className="portrait-ring portrait-ring-inner" cx="110" cy="108" r="72"/>
      <path className="portrait-shoulders" d="M39 227c4-37 20-57 47-67h48c28 10 44 30 48 67z" fill={`url(#${id}-suit)`}/>
      <path d="m86 157 24 24 24-24 12 8-21 44H95l-21-44z" fill="#f2f7f2"/>
      <path d="m99 177 11 11 11-11 9 10-8 40H98l-8-40z" fill="#45c6a7"/>
      <path d="M91 143h38v28c-10 12-28 12-38 0z" fill={`url(#${id}-face)`}/>
      <path d="M66 91c0-36 17-57 44-57s44 21 44 57v36c0 33-20 52-44 52s-44-19-44-52z" fill={`url(#${id}-face)`}/>
      <path d="M62 101c-3-45 15-72 49-72 35 0 52 25 48 70l-8-18-7-25c-19 16-44 23-73 21l-3 26z" fill="#26323b"/>
      <path d="M67 88c-9 3-13 12-12 23 1 9 6 15 13 16M153 88c9 3 13 12 12 23-1 9-6 15-13 16" fill="none" stroke="#d99d7d" strokeWidth="7" strokeLinecap="round"/>
      <path d="M82 106q8-6 17 0M121 106q8-6 17 0" fill="none" stroke="#704b40" strokeWidth="3.5" strokeLinecap="round"/>
      <ellipse className="portrait-eye" cx="91" cy="117" rx="3.1" ry="4" fill="#27383b"/><ellipse className="portrait-eye" cx="130" cy="117" rx="3.1" ry="4" fill="#27383b"/>
      <circle cx="92" cy="116" r="1" fill="#fff"/><circle cx="131" cy="116" r="1" fill="#fff"/>
      <path d="M110 117q-2 11-5 18 4 3 9 0" fill="none" stroke="#b87961" strokeWidth="2" strokeLinecap="round"/>
      <path className="pilot-mouth-closed" d="M99 143q11 7 22 0" fill="none" stroke="#935a51" strokeWidth="2.8" strokeLinecap="round"/>
      <ellipse className="pilot-mouth-open" cx="110" cy="144" rx="7" ry="2" fill="#733f3e"/>
      <path className="portrait-headset" d="M58 105V89a52 52 0 0 1 104 0v16" fill="none" stroke="#58d8ba" strokeWidth="4" strokeLinecap="round"/>
      <rect x="54" y="99" width="10" height="23" rx="5" fill="#287c70"/><rect x="156" y="99" width="10" height="23" rx="5" fill="#287c70"/>
      <path className="portrait-mic" d="M160 116q7 18-10 25h-10" fill="none" stroke="#58d8ba" strokeWidth="2.5" strokeLinecap="round"/>
      <circle className="portrait-mic-dot" cx="137" cy="141" r="3.5" fill="#58d8ba"/>
      <circle className="portrait-spark spark-a" cx="34" cy="91" r="3"/><circle className="portrait-spark spark-b" cx="187" cy="151" r="2.5"/>
    </svg>
  </div>;
});
