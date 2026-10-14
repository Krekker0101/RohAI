import { memo, useEffect, useRef, type RefObject } from 'react';
import type { AnimationState } from './state';

type Body = 'listening' | 'thinking' | 'explaining';
interface Mouth { x: number; y: number; width: number; rotation: number }
interface Clip { src: string; poster: string; fps: number; width: number; height: number; mouth: Mouth[]; audio_tracks: number }
type Clips = Record<Body, Clip>;
const base = `${import.meta.env.BASE_URL}pilot/`;
let manifest: Promise<Clips> | undefined;
function loadClips() {
  return manifest ??= fetch(`${base}animations.json`).then(async response => {
    if (!response.ok) throw new Error('Pilot animation assets unavailable');
    const clips = await response.json() as Clips;
    for (const clip of Object.values(clips)) {
      if (clip.audio_tracks !== 0 || !clip.mouth.length || clip.fps <= 0 || !/^[a-z]+\.mp4$/.test(clip.src)) throw new Error('Invalid silent animation');
    }
    return clips;
  }).catch(error => { manifest = undefined; throw error; });
}
function bodyFor(state: AnimationState): Body {
  if (state === 'LISTENING' || state === 'HEARING') return 'listening';
  if (state === 'SPEAKING' || state === 'EXPLAINING' || state === 'HAPPY') return 'explaining';
  return 'thinking';
}

/** Body pixels and tracked mouth are composed only at playback time. */
export const VideoAvatar = memo(function VideoAvatar({ state, audioLevel }: { state: AnimationState; audioLevel?: RefObject<number> }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(state);
  useEffect(() => { latest.current = state; }, [state]);
  useEffect(() => {
    let disposed = false;
    let frame = 0;
    let lastDraw = 0;
    let current: Body = bodyFor(latest.current);
    let outgoing: Body | null = null;
    let changedAt = 0;
    const videos = new Map<Body, HTMLVideoElement>();
    const starting = new Set<Body>();
    const posters = new Map<Body, HTMLImageElement>();
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const stopVideos = () => videos.forEach(video => video.pause());
    const visibility = () => { if (document.hidden) stopVideos(); };
    document.addEventListener('visibilitychange', visibility);

    void loadClips().then(clips => {
      if (disposed || !canvas.current) return;
      const surface = canvas.current;
      const context = surface.getContext('2d');
      if (!context) return;
      for (const name of Object.keys(clips) as Body[]) {
        const video = document.createElement('video');
        video.muted = true; video.volume = 0; video.loop = true; video.playsInline = true; video.preload = 'metadata';
        video.src = `${base}${clips[name].src}`;
        videos.set(name, video);
        const poster = new Image(); poster.src = `${base}${clips[name].poster}`; posters.set(name, poster);
      }
      const layer = (name: Body, alpha: number, opening: number) => {
        if (alpha <= 0) return;
        const clip = clips[name]; const video = videos.get(name)!; const poster = posters.get(name)!;
        context.save(); context.globalAlpha = alpha;
        if (video.readyState >= 2) context.drawImage(video, 0, 0, surface.width, surface.height);
        else if (poster.complete && poster.naturalWidth) context.drawImage(poster, 0, 0, surface.width, surface.height);
        else { context.restore(); return; }
        const index = Math.min(clip.mouth.length - 1, Math.max(0, Math.floor(video.currentTime * clip.fps)));
        const mouth = clip.mouth[index];
        const width = mouth.width * surface.width;
        context.translate(mouth.x * surface.width, mouth.y * surface.height);
        context.rotate(mouth.rotation * Math.PI / 180);
        context.lineCap = 'round';
        if (opening < 0.035) {
          context.beginPath(); context.moveTo(-width * 0.45, 0);
          context.quadraticCurveTo(0, width * 0.12, width * 0.45, 0);
          context.strokeStyle = '#92513c'; context.lineWidth = 2.2; context.stroke();
        } else {
          const height = Math.max(1.5, width * 0.27 * opening);
          context.beginPath(); context.ellipse(0, 1, width * 0.45, height, 0, 0, Math.PI * 2);
          context.fillStyle = '#542c29'; context.fill();
          context.strokeStyle = '#ae614b'; context.lineWidth = 2.5; context.stroke();
          context.save(); context.clip();
          context.fillStyle = '#fff2df'; context.fillRect(-width * 0.36, 1 - height, width * 0.72, Math.min(height * 0.4, 5));
          context.fillStyle = '#c57570'; context.beginPath(); context.ellipse(0, height * 0.7, width * 0.22, height * 0.25, 0, 0, Math.PI * 2); context.fill();
          context.restore();
        }
        context.restore();
      };
      const animate = (now: number) => {
        if (disposed) return;
        frame = requestAnimationFrame(animate);
        if (document.hidden || now - lastDraw < 1000 / 30) return;
        lastDraw = now;
        const desired = bodyFor(latest.current);
        if (desired !== current) {
          if (outgoing) videos.get(outgoing)?.pause();
          outgoing = current; current = desired; changedAt = now;
          videos.get(current)!.currentTime = 0;
        }
        const still = reduced.matches || latest.current === 'SLEEPING';
        for (const [name, video] of videos) {
          if (still || (name !== current && name !== outgoing)) video.pause();
          else if (video.paused && !starting.has(name)) {
            starting.add(name);
            void video.play().catch(() => {}).finally(() => starting.delete(name));
          }
        }
        const blend = outgoing ? Math.min(1, (now - changedAt) / 280) : 1;
        // This value comes from the analyser of actually playing PCM, never from a timer.
        const opening = latest.current === 'SPEAKING' ? Math.min(1, Math.max(0, audioLevel?.current ?? 0)) : 0;
        surface.dataset.body = current; surface.dataset.mouthLevel = opening.toFixed(3);
        surface.dataset.frame = String(Math.floor(videos.get(current)!.currentTime * clips[current].fps));
        context.clearRect(0, 0, surface.width, surface.height);
        context.fillStyle = '#080c10'; context.fillRect(0, 0, surface.width, surface.height);
        if (outgoing) layer(outgoing, 1, opening);
        layer(current, blend, opening);
        if (outgoing && blend === 1) { videos.get(outgoing)?.pause(); outgoing = null; }
      };
      frame = requestAnimationFrame(animate);
    }).catch(() => { if (!disposed && canvas.current) canvas.current.dataset.error = 'assets-unavailable'; });
    return () => {
      disposed = true; cancelAnimationFrame(frame); document.removeEventListener('visibilitychange', visibility);
      videos.forEach(video => { video.pause(); video.removeAttribute('src'); video.load(); });
    };
  }, [audioLevel]);
  return <canvas ref={canvas} width={544} height={704} className="pilot-body-canvas" style={{ backgroundImage: `url(${base}listening.jpg)`, backgroundSize: 'cover' }} aria-hidden="true"/>;
});
