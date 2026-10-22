import { useEffect, useRef, useState } from 'react';
import { VideoOff } from 'lucide-react';
import { backendUrl } from '../data/backend';
import { runtimePath } from '../data/source';
import { clock, phaseNames, type Snapshot } from '../domain/telemetry';
import { Intersection } from './Intersection';

export function VideoFeed({ active, snapshot, demo: demoMode = false }: { active: boolean; snapshot: Snapshot | null; demo?: boolean }) {
  const image = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);
  const demo = demoMode || snapshot?.mode === 'demo';
  useEffect(() => {
    if (!active || demo) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let url: string | null = null;
    async function next() {
      try {
        if (!document.hidden) {
          const response = await fetch(backendUrl(runtimePath('/api/v1/vision/frame.jpg')), { cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(3000)]) });
          if (!response.ok) throw new Error('Frame unavailable');
          const blob = await response.blob();
          if (controller.signal.aborted) return;
          const previous = url; url = URL.createObjectURL(blob);
          if (image.current) image.current.src = url;
          if (previous) URL.revokeObjectURL(previous);
          setFailed(false);
        }
      } catch { if (!controller.signal.aborted) setFailed(true); }
      if (!controller.signal.aborted) timer = setTimeout(() => void next(), 200);
    }
    void next();
    return () => { controller.abort(); clearTimeout(timer); if (url) URL.revokeObjectURL(url); };
  }, [active, demo]);
  if (demo && snapshot) {
    const directions = { north: 'СЕВЕР', south: 'ЮГ', east: 'ВОСТОК', west: 'ЗАПАД' };
    const stages: Record<string, string> = { green: 'ЗЕЛЁНЫЙ', yellow: 'ЖЁЛТЫЙ', all_red: 'ВСЕМ СТОП' };
    const stage = stages[snapshot.signals.stage] ?? snapshot.signals.stage;
    return <div className="demo-camera-feed">
      <Intersection snapshot={snapshot} safe={active} cameraOverlay />
      <section className="demo-camera-hud demo-camera-queues"><b>DEMO VISION · ПОТОК</b>{snapshot.lanes.map(lane=><span key={lane.direction}>{directions[lane.direction]} <i>очередь {lane.queue}</i><strong>score {lane.score.toFixed(1)}</strong></span>)}</section>
      <section className="demo-camera-hud demo-camera-state"><small>СОСТОЯНИЕ СИСТЕМЫ</small><b>{phaseNames[snapshot.signals.phase]} · {stage}</b><span>{Object.entries(snapshot.lamps).map(([direction, signal])=>`${direction[0].toUpperCase()}:${signal==='green'?'З':'К'}`).join(' · ')}</span></section>
      <div className="demo-camera-disclaimer">DEMO DATA — синтетический поток и демонстрационные рамки, не изображение с камеры.</div>
      <div className="demo-camera-clock">t = {clock(snapshot.time)} · #{snapshot.sequence}</div>
    </div>;
  }
  return <div className="video-feed"><img ref={image} alt="Видео с детекциями, треками, ROI и сигналами" hidden={!active || failed} />{!active || failed ? <div className="empty"><VideoOff size={32} /><h3>Нет свежего видеокадра</h3><p>Видеопоток появится, когда Vision Engine будет готов.</p></div> : null}</div>;
}
