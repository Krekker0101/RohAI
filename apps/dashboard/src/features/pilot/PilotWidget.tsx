import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Send, Square, Volume2, VolumeX, RotateCcw, Trash2, ShieldCheck, X, Sparkles } from 'lucide-react';
import { Dialog } from '../../components/Dialog';
import { request } from '../../data/api';
import { Avatar } from './Avatar';
import { ask, speech, transcribe, type Answer, type Capabilities, type PilotLanguage, type PilotLevel, type PilotPage } from './api';
import { AudioPlaybackController, MicrophoneManager } from './Voice';
import { stateLabels, transition, type AnimationEvent, type AnimationState } from './state';
import './pilot.css';

interface Message { id: string; question: string; answer?: Answer; cancelled?: boolean; error?: string }
const statuses: Record<string, string> = { implemented: 'Реализовано', partial: 'Частично', experimental: 'Эксперимент', planned: 'Запланировано', unconfirmed: 'Не подтверждено' };
function readableError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (/NotAllowed|Permission|denied/i.test(text)) return 'Доступ к микрофону не разрешён. Можно написать вопрос.';
  if (/NotFound|DevicesNotFound/i.test(text)) return 'Микрофон не найден. Проверьте устройство ввода в настройках Windows.';
  if (/NotReadable|TrackStart/i.test(text)) return 'Не удалось открыть микрофон. Проверьте, не занят ли он другой программой.';
  if (/PILOT_STT_UNAVAILABLE/.test(text)) return 'Распознавание речи недоступно. Проверьте локальную модель Whisper и журнал сервера.';
  if (/PILOT_BUSY|429/.test(text)) return 'Пилот занят или достигнут лимит запросов. Попробуйте немного позже.';
  if (/401|AUTH/.test(text)) return 'Нужен токен viewer или operator из настроек подключения.';
  if (/PILOT_REQUEST_CANCELLED/.test(text)) return 'Запрос отменён сервером. Отправьте вопрос повторно.';
  if (/SESSION_NOT_FOUND/.test(text)) return 'Диалог истёк. Очистите историю и задайте вопрос заново.';
  if (/GROUNDING/.test(text)) return 'Модель не смогла подтвердить ответ источниками. Уточните вопрос.';
  if (/GENERATION|LANGUAGE_UNAVAILABLE/.test(text)) return 'Модель не смогла сформулировать проверяемый ответ на выбранном языке. Уточните вопрос или попробуйте другой язык.';
  if (/503|INFERENCE|WORKER|PROVIDER|Failed to fetch|NetworkError/.test(text)) return 'Локальная модель или backend недоступны. Проверьте запуск Пилота; текстовый вопрос можно повторить.';
  if (/Timeout|AbortError/.test(text)) return 'Ожидание ответа истекло. Можно повторить вопрос.';
  return text;
}

export default memo(function PilotWidget({ page, mode }: { page: PilotPage; mode: 'demo' | 'real' }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<AnimationState>('IDLE');
  const [input, setInput] = useState(''); const [messages, setMessages] = useState<Message[]>([]);
  const [caps, setCaps] = useState<Capabilities | null>(null); const [error, setError] = useState('');
  const [recognizing, setRecognizing] = useState(false);
  const [muted, setMuted] = useState(false); const [language, setLanguage] = useState<PilotLanguage>('ru');
  const [level, setLevel] = useState<PilotLevel>('auto');
  const conversation = useRef<string | null>(null); const controller = useRef<AbortController | null>(null);
  const audioController = useRef<AbortController | null>(null);
  const audioLevel = useRef(0);
  const epoch = useRef(0); const stage = useRef<HTMLDivElement>(null); const transcript = useRef<HTMLDivElement>(null);
  const microphone = useRef<MicrophoneManager | null>(null); const playback = useRef<AudioPlaybackController | null>(null);
  const panelOpen = useRef(false); const soundMuted = useRef(false);
  const event = useCallback((value: AnimationEvent) => setState(current => transition(current, value)), []);
  const levelChanged = useCallback((value: number) => { audioLevel.current = value; stage.current?.style.setProperty('--voice', value.toFixed(3)); }, []);
  const getMicrophone = () => microphone.current ??= new MicrophoneManager();
  const getPlayback = () => playback.current ??= new AudioPlaybackController(levelChanged, () => event('PLAY'), () => event('FINISH'));

  const stop = useCallback(() => {
    setRecognizing(false);
    epoch.current++; controller.current?.abort(); controller.current = null;
    audioController.current?.abort(); audioController.current = null;
    microphone.current?.stop(); playback.current?.stop(); levelChanged(0); event('STOP');
    setMessages(current => current.map(message => !message.answer && !message.error ? { ...message, cancelled: true } : message));
  }, [event, levelChanged]);
  const close = useCallback(() => { panelOpen.current = false; stop(); playback.current?.dispose(); setOpen(false); }, [stop]);

  useEffect(() => {
    const visibility = () => {
      if (document.hidden) {
        microphone.current?.stop();
        audioController.current?.abort();
        playback.current?.dispose();
        levelChanged(0);
        setState(current => current === 'THINKING' ? current : 'SLEEPING');
      } else setState(current => current === 'SLEEPING' ? 'IDLE' : current);
    };
    document.addEventListener('visibilitychange', visibility);
    return () => { document.removeEventListener('visibilitychange', visibility); controller.current?.abort(); audioController.current?.abort(); microphone.current?.stop(); playback.current?.dispose(); };
  }, [levelChanged]);
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    void request<Capabilities>('/api/v1/pilot/capabilities', 'GET', undefined, abort.signal).then(value => { if (!abort.signal.aborted) { setCaps(value); setError(''); } }).catch(reason => { if (!abort.signal.aborted) setError(readableError(reason)); });
    return () => abort.abort();
  }, [open]);
  useEffect(() => { transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: 'instant' }); }, [messages, state]);
  useEffect(() => {
    if (state !== 'IDLE' && state !== 'HAPPY' && state !== 'EXPLAINING' && state !== 'CONFUSED') return;
    const timer = setTimeout(() => event('SLEEP'), 90000); return () => clearTimeout(timer);
  }, [state, event]);

  async function speakAnswer(answer: Answer, turn: number): Promise<void> {
    if (soundMuted.current || turn !== epoch.current || !panelOpen.current || document.hidden) return;
    const abort = new AbortController(); audioController.current = abort;
    const timeout = setTimeout(() => abort.abort(), 120000);
    try {
      const audio = await speech(answer, abort.signal);
      if (turn !== epoch.current || abort.signal.aborted || soundMuted.current) return;
      await getPlayback().consume(audio, abort.signal);
    } catch (reason) { if (!abort.signal.aborted && !soundMuted.current) throw reason; }
    finally { clearTimeout(timeout); }
  }
  async function submit(text: string): Promise<void> {
    const question = text.trim(); if (!question) return;
    stop(); setError(''); setInput('');
    const turn = epoch.current; const abort = new AbortController(); controller.current = abort;
    // A cold CPU inference and one validation retry may each take up to 180s.
    const timeout = setTimeout(() => abort.abort(), 370000); const id = crypto.randomUUID();
    setMessages(current => [...current.slice(-7), { id, question }]); event('ASK');
    // Unlock Web Audio during user gesture; no sound plays until real samples arrive.
    if (!soundMuted.current) void getPlayback().prepare().catch(() => { soundMuted.current = true; setMuted(true); setError('Браузер ограничил звук. Включите его вручную; ответ будет текстом.'); });
    try {
      const answer = await ask(question, conversation.current, page, mode, level, language, abort.signal);
      if (turn !== epoch.current || abort.signal.aborted || !panelOpen.current) return;
      conversation.current = answer.conversation_id;
      setMessages(current => current.map(message => message.id === id ? { ...message, answer } : message));
      event(answer.outcome === 'grounded' ? 'ANSWER' : 'UNCERTAIN');
      await speakAnswer(answer, turn);
    } catch (reason) {
      if (turn === epoch.current && panelOpen.current) {
        const message = readableError(reason);
        setError(message);
        setMessages(current => current.map(item => item.id === id && !item.answer ? { ...item, error: message } : item));
        event('FAIL');
      }
    }
    finally { clearTimeout(timeout); if (controller.current === abort) controller.current = null; }
  }
  async function listen(): Promise<void> {
    if (state === 'LISTENING' || state === 'HEARING') { await getMicrophone().finish(); return; }
    stop(); setError(''); setRecognizing(false); event('LISTEN'); const turn = epoch.current;
    try {
      await getMicrophone().start(value => { levelChanged(value); event(value > 0.024 ? 'HEAR' : 'SILENCE'); }, audio => {
        if (turn !== epoch.current || !panelOpen.current) return;
        levelChanged(0); setRecognizing(true); event('ASK');
        const abort = new AbortController(); controller.current = abort;
        const timeout = setTimeout(() => abort.abort(), 120000);
        void transcribe(audio, language, abort.signal).then(text => {
          if (turn !== epoch.current || !panelOpen.current) return;
          if (!text.trim()) { setError('Речь не распознана. Повторите фразу или напишите вопрос.'); event('FAIL'); return; }
          setRecognizing(false); setInput(text); void submit(text);
        }).catch(reason => { if (turn === epoch.current) { setError(readableError(reason)); event('FAIL'); } }).finally(() => { clearTimeout(timeout); if (turn === epoch.current) setRecognizing(false); });
      }, reason => { if (turn === epoch.current) { levelChanged(0); setError(readableError(reason)); event('FAIL'); } });
    } catch (reason) { if (turn === epoch.current) { getMicrophone().stop(); setError(readableError(reason)); event('FAIL'); } }
  }
  function toggleSound() {
    soundMuted.current = !soundMuted.current; setMuted(soundMuted.current);
    if (soundMuted.current) { audioController.current?.abort(); playback.current?.stop(); setState(current => current === 'SPEAKING' ? 'EXPLAINING' : current); }
    else void getPlayback().prepare().catch(reason => { setError(readableError(reason)); setMuted(true); soundMuted.current = true; });
  }
  async function repeat() {
    const answer = [...messages].reverse().find(message => message.answer)?.answer; if (!answer) return;
    stop(); soundMuted.current = false; setMuted(false); setError('');
    const turn = epoch.current; const abort = new AbortController(); controller.current = abort;
    try { await getPlayback().prepare(); await speakAnswer(answer, turn); }
    catch (reason) { if (turn === epoch.current) { setError(readableError(reason)); event('FAIL'); } }
  }
  function clearHistory() {
    stop(); const id = conversation.current; conversation.current = null; setMessages([]); setInput(''); setError('');
    if (id) void request('/api/v1/pilot/clear', 'POST', { conversation_id: id }).catch(() => setError('История вкладки очищена. Недоступная серверная сессия удалится по таймауту.'));
  }
  const listening = state === 'LISTENING' || state === 'HEARING';
  const pending = state === 'THINKING';
  const lastAnswer = [...messages].reverse().some(message => message.answer);
  return <>
    <button className="pilot-launcher" aria-label="Открыть Пилота" aria-haspopup="dialog" onClick={() => { panelOpen.current = true; setOpen(true); event('OPEN'); }}>
      <Avatar state={state === 'SLEEPING' ? state : open ? state : 'IDLE'} compact/><span>PILOT <b>AI</b></span>
    </button>
    {open ? <Dialog title="Пилот · ваш гид по RohAI" onClose={close}>
      <div className="pilot-panel">
        <div className="pilot-hero" ref={stage}>
          <div className="pilot-orbit" aria-hidden="true"/><Avatar state={state} audioLevel={audioLevel}/>
          <div className="pilot-intro"><span className="pilot-kicker"><Sparkles size={13}/> ЛОКАЛЬНЫЙ AI</span><h3>Давайте разберёмся.</h3><p>Помогаю разобраться в RohAI по источникам проекта.</p><div className="pilot-status" role="status">{recognizing && state === 'THINKING' ? 'Распознаю русскую или английскую речь…' : stateLabels[state]}</div></div>
        </div>
        <div className="pilot-preferences">
          <label>Язык<select aria-label="Язык Пилота" value={language} onChange={e => { stop(); setLanguage(e.target.value as PilotLanguage); }}><option value="ru">Русский</option><option value="en">English</option><option value="tg" disabled>Тоҷикӣ — голос ещё не проверен</option></select></label>
          <label>Объяснение<select aria-label="Уровень объяснения" value={level} onChange={e => setLevel(e.target.value as PilotLevel)}><option value="auto">Автоматически</option><option value="simple">Проще</option><option value="standard">Обычно</option><option value="technical">Технически</option></select></label>
          <button className="pilot-control" onClick={toggleSound} aria-label={muted ? 'Включить звук Пилота' : 'Отключить звук Пилота'} aria-pressed={!muted}>{muted ? <VolumeX size={18}/> : <Volume2 size={18}/>}</button>
        </div>
        <div className="pilot-context"><ShieldCheck size={13}/> Только чтение · раздел {page} · {mode.toUpperCase()} · история временная</div>
        {caps && !caps.chat_available ? <p className="pilot-warning" role="status">Локальная модель ещё не установлена. Подготовьте Пилота по инструкции PILOT_AI.md.</p> : null}
        {caps?.chat_available && caps.generation_status === 'experimental' ? <p className="pilot-warning">Генерация ответов экспериментальная. Сверяйте сведения с источниками; неподтверждённая формулировка может быть отклонена.</p> : null}
        <div className="pilot-transcript" ref={transcript} role="log" aria-label="Диалог с Пилотом" aria-live="polite">
          {!messages.length ? <div className="pilot-welcome"><b>С чего начнём?</b><p>Спросите о видео, дороге, аналитике или возможностях проекта.</p><div>{['Какие основные возможности проекта?', 'Как загрузить видео?', 'Что показывает этот раздел?'].map(question => <button key={question} onClick={() => void submit(question)}>{question}</button>)}</div></div> : null}
          {messages.map(message => <div className="pilot-turn" key={message.id}><p className="pilot-question"><span>Вы</span>{message.question}</p>{message.answer ? <div className="pilot-answer"><span>Пилот</span><p>{message.answer.answer}</p>{message.answer.sources.length ? <details><summary>Источники ответа · {message.answer.sources.length}</summary>{message.answer.sources.map(source => <div className="pilot-source" key={source.id}><b>{source.title}</b><em>{statuses[source.status] ?? source.status}</em>{source.files.map(file => <code key={file.path}>{file.path}</code>)}</div>)}</details> : null}</div> : <p className="pilot-pending">{message.error ? 'Не удалось получить ответ. Можно отправить вопрос повторно.' : message.cancelled ? 'Ответ остановлен вами' : 'Проверяю сведения…'}</p>}</div>)}
        </div>
        {error ? <div className="pilot-error" role="alert"><span>{error}</span><button aria-label="Скрыть ошибку Пилота" onClick={() => setError('')}><X size={16}/></button></div> : null}
        <form className="pilot-composer" onSubmit={e => { e.preventDefault(); void submit(input); }}>
          <label className="pilot-input">Ваш вопрос<textarea autoFocus aria-label="Вопрос Пилоту" rows={2} maxLength={1200} value={input} placeholder="Напишите или продиктуйте вопрос о RohAI…" onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(input); } }}/></label>
          <div className="pilot-actions"><button type="button" className={`pilot-control ${listening ? 'active' : ''}`} onClick={() => void listen()} aria-label={listening ? 'Завершить запись фразы' : 'Задать вопрос голосом'} disabled={!caps?.stt_available}>{listening ? <><MicOff size={19}/> Завершить запись</> : <Mic size={19}/>}</button><button type="submit" className="primary-button" disabled={!input.trim()}><Send size={16}/>{pending ? 'Новый вопрос' : 'Отправить'}</button><button type="button" className="pilot-control" onClick={stop} aria-label="Остановить Пилота"><Square size={17}/></button><button type="button" className="pilot-control" onClick={() => void repeat()} disabled={!lastAnswer} aria-label="Повторить ответ"><RotateCcw size={17}/></button><button type="button" className="pilot-control" onClick={clearHistory} aria-label="Очистить диалог"><Trash2 size={17}/></button></div>
        </form>
        <p className="pilot-privacy">Микрофон включается по нажатию. Распознанная фраза автоматически отправляется как вопрос. Аудио обрабатывается локально и не сохраняется.</p>
      </div>
    </Dialog> : null}
  </>;
});
