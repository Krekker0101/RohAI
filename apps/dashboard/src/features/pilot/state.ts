export type AnimationState = 'IDLE' | 'LISTENING' | 'HEARING' | 'THINKING' | 'SPEAKING' | 'HAPPY' | 'CONFUSED' | 'EXPLAINING' | 'ERROR' | 'SLEEPING';
export type AnimationEvent = 'OPEN' | 'LISTEN' | 'HEAR' | 'SILENCE' | 'ASK' | 'ANSWER' | 'UNCERTAIN' | 'PLAY' | 'FINISH' | 'STOP' | 'FAIL' | 'SLEEP';
export function transition(state: AnimationState, event: AnimationEvent): AnimationState {
  switch (event) {
    case 'OPEN': case 'STOP': return 'IDLE';
    case 'LISTEN': return 'LISTENING';
    case 'HEAR': return state === 'LISTENING' || state === 'HEARING' ? 'HEARING' : state;
    case 'SILENCE': return state === 'HEARING' ? 'LISTENING' : state;
    case 'ASK': return 'THINKING';
    case 'ANSWER': return 'EXPLAINING';
    case 'UNCERTAIN': return 'CONFUSED';
    case 'PLAY': return 'SPEAKING';
    case 'FINISH': return state === 'SPEAKING' ? 'HAPPY' : state;
    case 'FAIL': return 'ERROR';
    case 'SLEEP': return 'SLEEPING';
  }
}
export const stateLabels: Record<AnimationState, string> = {
  IDLE: 'Готов помочь', LISTENING: 'Слушаю — скажите вопрос', HEARING: 'Слышу вашу фразу',
  THINKING: 'Проверяю источники проекта', SPEAKING: 'Говорю', HAPPY: 'Рад помочь',
  CONFUSED: 'Нужен контекст проекта', EXPLAINING: 'Объясняю по источникам',
  ERROR: 'Нужна проверка подключения', SLEEPING: 'Отдыхаю',
};
