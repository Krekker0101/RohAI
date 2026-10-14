import { memo, type RefObject } from 'react';
import type { AnimationState } from './state';
import { VideoAvatar } from './VideoAvatar';

export const Avatar = memo(function Avatar({ state, compact = false, audioLevel }: { state: AnimationState; compact?: boolean; audioLevel?: RefObject<number> }) {
  return <div className={`pilot-avatar ${compact ? 'compact' : 'video-character'}`} data-state={state} role="img" aria-label={`Пилот: ${state}`}>
    {compact ? <img src={`${import.meta.env.BASE_URL}pilot/listening.jpg`} alt=""/> : <VideoAvatar state={state} audioLevel={audioLevel}/>}
  </div>;
});
