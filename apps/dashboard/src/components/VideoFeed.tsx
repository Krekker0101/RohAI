import { useEffect, useRef, useState } from 'react';
import { VideoOff } from 'lucide-react';
import { backendUrl } from '../data/backend';
import { runtimePath } from '../data/source';

export function VideoFeed({ active }: { active: boolean }) {
  const image = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!active) return;
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
  }, [active]);
  return <div className="video-feed"><img ref={image} alt="Видео с детекциями, треками, ROI и сигналами" hidden={!active || failed} />{!active || failed ? <div className="empty"><VideoOff size={32} /><h3>Нет свежего видеокадра</h3><p>Видеопоток появится, когда Vision Engine будет готов.</p></div> : null}</div>;
}
