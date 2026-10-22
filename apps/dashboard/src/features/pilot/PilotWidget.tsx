import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Send, Square, Volume2, VolumeX, RotateCcw, Trash2, ShieldCheck, X, Sparkles } from 'lucide-react';
import { Dialog } from '../../components/Dialog';
import { request } from '../../data/api';
import { Avatar } from './Avatar';
import { ask, speech, transcribe, type Answer, type Capabilities, type PilotLanguage, type PilotLevel, type PilotPage } from './api';
import { AudioPlaybackController, MicrophoneManager } from './Voice';
import { stateLabels, transition, type AnimationEvent, type AnimationState } from './state';
import { Localized } from '../../i18n';
import './pilot.css';

interface Message { id: string; question: string; answer?: Answer; cancelled?: boolean; error?: string }
const statuses: Record<string, string> = { implemented: 'Р РµР°Р»РёР·РѕРІР°РЅРѕ', partial: 'Р§Р°СЃС‚РёС‡РЅРѕ', experimental: 'Р­РєСЃРїРµСЂРёРјРµРЅС‚', planned: 'Р—Р°РїР»Р°РЅРёСЂРѕРІР°РЅРѕ', unconfirmed: 'РќРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРѕ' };
function readableError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (/NotAllowed|Permission|denied/i.test(text)) return 'Р”РѕСЃС‚СѓРї Рє РјРёРєСЂРѕС„РѕРЅСѓ РЅРµ СЂР°Р·СЂРµС€С‘РЅ. РњРѕР¶РЅРѕ РЅР°РїРёСЃР°С‚СЊ РІРѕРїСЂРѕСЃ.';
  if (/NotFound|DevicesNotFound/i.test(text)) return 'РњРёРєСЂРѕС„РѕРЅ РЅРµ РЅР°Р№РґРµРЅ. РџСЂРѕРІРµСЂСЊС‚Рµ СѓСЃС‚СЂРѕР№СЃС‚РІРѕ РІРІРѕРґР° РІ РЅР°СЃС‚СЂРѕР№РєР°С… Windows.';
  if (/NotReadable|TrackStart/i.test(text)) return 'РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РєСЂС‹С‚СЊ РјРёРєСЂРѕС„РѕРЅ. РџСЂРѕРІРµСЂСЊС‚Рµ, РЅРµ Р·Р°РЅСЏС‚ Р»Рё РѕРЅ РґСЂСѓРіРѕР№ РїСЂРѕРіСЂР°РјРјРѕР№.';
  if (/PILOT_STT_UNAVAILABLE/.test(text)) return 'Р Р°СЃРїРѕР·РЅР°РІР°РЅРёРµ СЂРµС‡Рё РЅРµРґРѕСЃС‚СѓРїРЅРѕ. РџСЂРѕРІРµСЂСЊС‚Рµ Р»РѕРєР°Р»СЊРЅСѓСЋ РјРѕРґРµР»СЊ Whisper Рё Р¶СѓСЂРЅР°Р» СЃРµСЂРІРµСЂР°.';
  if (/PILOT_BUSY|429/.test(text)) return 'РџРёР»РѕС‚ Р·Р°РЅСЏС‚ РёР»Рё РґРѕСЃС‚РёРіРЅСѓС‚ Р»РёРјРёС‚ Р·Р°РїСЂРѕСЃРѕРІ. РџРѕРїСЂРѕР±СѓР№С‚Рµ РЅРµРјРЅРѕРіРѕ РїРѕР·Р¶Рµ.';
  if (/401|AUTH/.test(text)) return 'РќСѓР¶РµРЅ С‚РѕРєРµРЅ viewer РёР»Рё operator РёР· РЅР°СЃС‚СЂРѕРµРє РїРѕРґРєР»СЋС‡РµРЅРёСЏ.';
  if (/PILOT_REQUEST_CANCELLED/.test(text)) return 'Р—Р°РїСЂРѕСЃ РѕС‚РјРµРЅС‘РЅ СЃРµСЂРІРµСЂРѕРј. РћС‚РїСЂР°РІСЊС‚Рµ РІРѕРїСЂРѕСЃ РїРѕРІС‚РѕСЂРЅРѕ.';
  if (/SESSION_NOT_FOUND/.test(text)) return 'Р”РёР°Р»РѕРі РёСЃС‚С‘Рє. РћС‡РёСЃС‚РёС‚Рµ РёСЃС‚РѕСЂРёСЋ Рё Р·Р°РґР°Р№С‚Рµ РІРѕРїСЂРѕСЃ Р·Р°РЅРѕРІРѕ.';
  if (/GROUNDING/.test(text)) return 'РњРѕРґРµР»СЊ РЅРµ СЃРјРѕРіР»Р° РїРѕРґС‚РІРµСЂРґРёС‚СЊ РѕС‚РІРµС‚ РёСЃС‚РѕС‡РЅРёРєР°РјРё. РЈС‚РѕС‡РЅРёС‚Рµ РІРѕРїСЂРѕСЃ.';
  if (/GENERATION|LANGUAGE_UNAVAILABLE/.test(text)) return 'РњРѕРґРµР»СЊ РЅРµ СЃРјРѕРіР»Р° СЃС„РѕСЂРјСѓР»РёСЂРѕРІР°С‚СЊ РїСЂРѕРІРµСЂСЏРµРјС‹Р№ РѕС‚РІРµС‚ РЅР° РІС‹Р±СЂР°РЅРЅРѕРј СЏР·С‹РєРµ. РЈС‚РѕС‡РЅРёС‚Рµ РІРѕРїСЂРѕСЃ РёР»Рё РїРѕРїСЂРѕР±СѓР№С‚Рµ РґСЂСѓРіРѕР№ СЏР·С‹Рє.';
  if (/503|INFERENCE|WORKER|PROVIDER|Failed to fetch|NetworkError/.test(text)) return 'Р›РѕРєР°Р»СЊРЅР°СЏ РјРѕРґРµР»СЊ РёР»Рё backend РЅРµРґРѕСЃС‚СѓРїРЅС‹. РџСЂРѕРІРµСЂСЊС‚Рµ Р·Р°РїСѓСЃРє РџРёР»РѕС‚Р°; С‚РµРєСЃС‚РѕРІС‹Р№ РІРѕРїСЂРѕСЃ РјРѕР¶РЅРѕ РїРѕРІС‚РѕСЂРёС‚СЊ.';
  if (/Timeout|AbortError/.test(text)) return 'РћР¶РёРґР°РЅРёРµ РѕС‚РІРµС‚Р° РёСЃС‚РµРєР»Рѕ. РњРѕР¶РЅРѕ РїРѕРІС‚РѕСЂРёС‚СЊ РІРѕРїСЂРѕСЃ.';
  return text;
}

function questionLanguage(question: string, selected: PilotLanguage): PilotLanguage {
  const tajik =
    /[Т“Т›ТіТ·УЇУЈ]|(?:^|\s)(?:СЃР°Р»РѕРј|Р±Р°СЂРѕРё|Р±РѕСЂР°Рё|С‡УЈ|С‡Рё|РіСѓС„С‚Р°|РјРµС‚Р°РІРѕРЅ\w*|РјРµС…РѕТі\w*|Р»РѕРёТіР°|РјР°СЉР»СѓРјРѕС‚)(?=$|\s|[?!.,])/iu.test(
      question,
    );
  const russian =
    /[С‘С‹СЌС‰СЉ]|(?:^|\s)(?:РїСЂРёРІРµС‚|Р·РґСЂР°РІСЃС‚РІ\w*|С‡С‚Рѕ|РєР°Рє|РїРѕС‡РµРјСѓ|Р·Р°С‡РµРј|РіРґРµ|РєРѕРіРґР°|СЃРєРѕР»СЊРєРѕ|СЂР°СЃСЃРєР°Р¶Рё|РѕР±СЉСЏСЃРЅРё|РїРѕРєР°Р¶Рё|РјРѕР¶РЅРѕ|СЂР°Р±РѕС‚Р°РµС‚|РґР°РЅРЅС‹Рµ|РїРѕ-СЂСѓСЃСЃРєРё|СЂСѓСЃСЃРє\w*|СЃРµР№С‡Р°СЃ|РїРѕРЅРёРјР°\w*|РјРµРЅСЏ|РјРЅРµ|РїРѕР¶Р°Р»СѓР№СЃС‚Р°|РїРѕРјРѕРіРё|РёСЃРїСЂР°РІСЊ|СЃРґРµР»Р°Р№|РЅСѓР¶РЅРѕ|РЅР°РґРѕ)(?=$|\s|[?!.,])/iu.test(
      question,
    );
  if (tajik && !russian) return 'tg';
  if (russian && !tajik) return 'ru';
  return selected;
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
    const answerLanguage = questionLanguage(question, language);
    if (answerLanguage !== language) setLanguage(answerLanguage);
    stop(); setError(''); setInput('');
    const turn = epoch.current; const abort = new AbortController(); controller.current = abort;
    // A cold CPU inference and one validation retry may each take up to 180s.
    const timeout = setTimeout(() => abort.abort(), 370000); const id = crypto.randomUUID();
    setMessages(current => [...current.slice(-7), { id, question }]); event('ASK');
    // Unlock Web Audio during user gesture; no sound plays until real samples arrive.
    if (!soundMuted.current) void getPlayback().prepare().catch(() => { soundMuted.current = true; setMuted(true); setError('Р‘СЂР°СѓР·РµСЂ РѕРіСЂР°РЅРёС‡РёР» Р·РІСѓРє. Р’РєР»СЋС‡РёС‚Рµ РµРіРѕ РІСЂСѓС‡РЅСѓСЋ; РѕС‚РІРµС‚ Р±СѓРґРµС‚ С‚РµРєСЃС‚РѕРј.'); });
    try {
      const answer = await ask(question, conversation.current, page, mode, level, answerLanguage, abort.signal);
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
          if (!text.trim()) { setError('Р РµС‡СЊ РЅРµ СЂР°СЃРїРѕР·РЅР°РЅР°. РџРѕРІС‚РѕСЂРёС‚Рµ С„СЂР°Р·Сѓ РёР»Рё РЅР°РїРёС€РёС‚Рµ РІРѕРїСЂРѕСЃ.'); event('FAIL'); return; }
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
    if (id) void request('/api/v1/pilot/clear', 'POST', { conversation_id: id }).catch(() => setError('РСЃС‚РѕСЂРёСЏ РІРєР»Р°РґРєРё РѕС‡РёС‰РµРЅР°. РќРµРґРѕСЃС‚СѓРїРЅР°СЏ СЃРµСЂРІРµСЂРЅР°СЏ СЃРµСЃСЃРёСЏ СѓРґР°Р»РёС‚СЃСЏ РїРѕ С‚Р°Р№РјР°СѓС‚Сѓ.'));
  }
  const listening = state === 'LISTENING' || state === 'HEARING';
  const pending = state === 'THINKING';
  const lastAnswer = [...messages].reverse().find(message => message.answer)?.answer;
  return <Localized language={language === 'tg' ? 'tg' : 'ru'}><>
    <button className="pilot-launcher" aria-label="РћС‚РєСЂС‹С‚СЊ РџРёР»РѕС‚Р°" aria-haspopup="dialog" onClick={() => { panelOpen.current = true; setOpen(true); event('OPEN'); }}>
      <Avatar state={state === 'SLEEPING' ? state : open ? state : 'IDLE'} compact/><span>PILOT <b>AI</b></span>
    </button>
    {open ? <Dialog title="РџРёР»РѕС‚ В· РІР°С€ РіРёРґ РїРѕ RohAI" onClose={close}>
      <div className="pilot-panel">
        <div className="pilot-hero" ref={stage}>
          <Avatar state={state}/>
          <div className="pilot-intro"><span className="pilot-kicker"><Sparkles size={13}/> Р›РћРљРђР›Р¬РќР«Р™ AI</span><h3>Р”Р°РІР°Р№С‚Рµ СЂР°Р·Р±РµСЂС‘РјСЃСЏ.</h3><p>РџРѕРјРѕРіР°СЋ СЂР°Р·РѕР±СЂР°С‚СЊСЃСЏ РІ RohAI РїРѕ РёСЃС‚РѕС‡РЅРёРєР°Рј РїСЂРѕРµРєС‚Р°.</p><div className="pilot-status" role="status">{recognizing && state === 'THINKING' ? (language === 'tg' ? 'Р Р°СЃРїРѕР·РЅР°СЋ С‚Р°РґР¶РёРєСЃРєСѓСЋ СЂРµС‡СЊвЂ¦' : language === 'ru' ? 'Р Р°СЃРїРѕР·РЅР°СЋ СЂСѓСЃСЃРєСѓСЋ СЂРµС‡СЊвЂ¦' : 'Р Р°СЃРїРѕР·РЅР°СЋ Р°РЅРіР»РёР№СЃРєСѓСЋ СЂРµС‡СЊвЂ¦') : stateLabels[state]}</div></div>
        </div>
        <div className="pilot-preferences">
          <label>РЇР·С‹Рє<select aria-label="РЇР·С‹Рє РџРёР»РѕС‚Р°" value={language} onChange={e => { stop(); setLanguage(e.target.value as PilotLanguage); }}><option value="ru">Р СѓСЃСЃРєРёР№</option><option value="en">English</option><option value="tg" disabled={!caps?.languages.includes('tg')}>РўРѕТ·РёРєУЈ{!caps?.languages.includes('tg') ? ' вЂ” РЅР°СЃС‚СЂРѕР№С‚Рµ РјРѕРґРµР»Рё' : ''}</option></select></label>
          <label>РћР±СЉСЏСЃРЅРµРЅРёРµ<select aria-label="РЈСЂРѕРІРµРЅСЊ РѕР±СЉСЏСЃРЅРµРЅРёСЏ" value={level} onChange={e => setLevel(e.target.value as PilotLevel)}><option value="auto">РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРё</option><option value="simple">РџСЂРѕС‰Рµ</option><option value="standard">РћР±С‹С‡РЅРѕ</option><option value="technical">РўРµС…РЅРёС‡РµСЃРєРё</option></select></label>
          <button className="pilot-control" onClick={toggleSound} disabled={language === 'tg' && !caps?.tts_languages.includes('tg')} aria-label={muted ? 'Р’РєР»СЋС‡РёС‚СЊ Р·РІСѓРє РџРёР»РѕС‚Р°' : 'РћС‚РєР»СЋС‡РёС‚СЊ Р·РІСѓРє РџРёР»РѕС‚Р°'} aria-pressed={!muted}>{muted ? <VolumeX size={18}/> : <Volume2 size={18}/>}</button>
        </div>
        <div className="pilot-context"><ShieldCheck size={13}/> РўРѕР»СЊРєРѕ С‡С‚РµРЅРёРµ В· СЂР°Р·РґРµР» {page} В· {mode.toUpperCase()} В· РёСЃС‚РѕСЂРёСЏ РІСЂРµРјРµРЅРЅР°СЏ</div>
        {caps && !caps.chat_available ? <p className="pilot-warning" role="status">Р›РѕРєР°Р»СЊРЅР°СЏ РјРѕРґРµР»СЊ РµС‰С‘ РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅР°. РџРѕРґРіРѕС‚РѕРІСЊС‚Рµ РџРёР»РѕС‚Р° РїРѕ РёРЅСЃС‚СЂСѓРєС†РёРё PILOT_AI.md.</p> : null}
        {caps?.chat_available && caps.generation_status === 'experimental' ? <p className="pilot-warning">Р“РµРЅРµСЂР°С†РёСЏ РѕС‚РІРµС‚РѕРІ СЌРєСЃРїРµСЂРёРјРµРЅС‚Р°Р»СЊРЅР°СЏ. РЎРІРµСЂСЏР№С‚Рµ СЃРІРµРґРµРЅРёСЏ СЃ РёСЃС‚РѕС‡РЅРёРєР°РјРё; РЅРµРїРѕРґС‚РІРµСЂР¶РґС‘РЅРЅР°СЏ С„РѕСЂРјСѓР»РёСЂРѕРІРєР° РјРѕР¶РµС‚ Р±С‹С‚СЊ РѕС‚РєР»РѕРЅРµРЅР°.</p> : null}
        {language === 'tg' && caps && !caps.tts_languages.includes('tg') ? <p className="pilot-warning" role="status">{'\u0422\u0435\u043a\u0441\u0442 \u0438 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0432\u0430\u043d\u0438\u0435 \u043f\u043e-\u0442\u0430\u0434\u0436\u0438\u043a\u0441\u043a\u0438 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b; \u0434\u043b\u044f \u043e\u0437\u0432\u0443\u0447\u0438\u0432\u0430\u043d\u0438\u044f \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u0435 \u043c\u043e\u0434\u0435\u043b\u044c \u043f\u043e docs/PILOT_TAJIK.md.'}</p> : null}
        <div className="pilot-transcript" ref={transcript} role="log" aria-label="Р”РёР°Р»РѕРі СЃ РџРёР»РѕС‚РѕРј" aria-live="polite">
          {!messages.length ? <div className="pilot-welcome"><b>РЎ С‡РµРіРѕ РЅР°С‡РЅС‘Рј?</b><p>РЎРїСЂРѕСЃРёС‚Рµ Рѕ РІРёРґРµРѕ, РґРѕСЂРѕРіРµ, Р°РЅР°Р»РёС‚РёРєРµ РёР»Рё РІРѕР·РјРѕР¶РЅРѕСЃС‚СЏС… РїСЂРѕРµРєС‚Р°.</p><div>{['РљР°РєРёРµ РѕСЃРЅРѕРІРЅС‹Рµ РІРѕР·РјРѕР¶РЅРѕСЃС‚Рё РїСЂРѕРµРєС‚Р°?', 'РљР°Рє Р·Р°РіСЂСѓР·РёС‚СЊ РІРёРґРµРѕ?', 'Р§С‚Рѕ РїРѕРєР°Р·С‹РІР°РµС‚ СЌС‚РѕС‚ СЂР°Р·РґРµР»?'].map(question => <button key={question} onClick={() => void submit(question)}>{question}</button>)}</div></div> : null}
          {messages.map(message => <div className="pilot-turn" key={message.id}><p className="pilot-question"><span>Р’С‹</span>{message.question}</p>{message.answer ? <div className="pilot-answer"><span>РџРёР»РѕС‚</span><p>{message.answer.answer}</p>{message.answer.sources.length ? <details><summary>РСЃС‚РѕС‡РЅРёРєРё РѕС‚РІРµС‚Р° В· {message.answer.sources.length}</summary>{message.answer.sources.map(source => <div className="pilot-source" key={source.id}><b>{source.title}</b><em>{statuses[source.status] ?? source.status}</em>{source.files.map(file => <code key={file.path}>{file.path}</code>)}</div>)}</details> : null}</div> : <p className="pilot-pending">{message.error ? 'РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕР»СѓС‡РёС‚СЊ РѕС‚РІРµС‚. РњРѕР¶РЅРѕ РѕС‚РїСЂР°РІРёС‚СЊ РІРѕРїСЂРѕСЃ РїРѕРІС‚РѕСЂРЅРѕ.' : message.cancelled ? 'РћС‚РІРµС‚ РѕСЃС‚Р°РЅРѕРІР»РµРЅ РІР°РјРё' : 'РџСЂРѕРІРµСЂСЏСЋ СЃРІРµРґРµРЅРёСЏвЂ¦'}</p>}</div>)}
        </div>
        {error ? <div className="pilot-error" role="alert"><span>{error}</span><button aria-label="РЎРєСЂС‹С‚СЊ РѕС€РёР±РєСѓ РџРёР»РѕС‚Р°" onClick={() => setError('')}><X size={16}/></button></div> : null}
        <form className="pilot-composer" onSubmit={e => { e.preventDefault(); void submit(input); }}>
          <label className="pilot-input">Р’Р°С€ РІРѕРїСЂРѕСЃ<textarea autoFocus aria-label="Р’РѕРїСЂРѕСЃ РџРёР»РѕС‚Сѓ" rows={2} maxLength={1200} value={input} placeholder="РќР°РїРёС€РёС‚Рµ РёР»Рё РїСЂРѕРґРёРєС‚СѓР№С‚Рµ РІРѕРїСЂРѕСЃ Рѕ RohAIвЂ¦" onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(input); } }}/></label>
          <div className="pilot-actions"><button type="button" className={`pilot-control ${listening ? 'active' : ''}`} onClick={() => void listen()} aria-label={listening ? 'Р—Р°РІРµСЂС€РёС‚СЊ Р·Р°РїРёСЃСЊ С„СЂР°Р·С‹' : 'Р—Р°РґР°С‚СЊ РІРѕРїСЂРѕСЃ РіРѕР»РѕСЃРѕРј'} disabled={!caps?.stt_available}>{listening ? <><MicOff size={19}/> Р—Р°РІРµСЂС€РёС‚СЊ Р·Р°РїРёСЃСЊ</> : <Mic size={19}/>}</button><button type="submit" className="primary-button" disabled={!input.trim()}><Send size={16}/>{pending ? 'РќРѕРІС‹Р№ РІРѕРїСЂРѕСЃ' : 'РћС‚РїСЂР°РІРёС‚СЊ'}</button><button type="button" className="pilot-control" onClick={stop} aria-label="РћСЃС‚Р°РЅРѕРІРёС‚СЊ РџРёР»РѕС‚Р°"><Square size={17}/></button><button type="button" className="pilot-control" onClick={() => void repeat()} disabled={!lastAnswer || (lastAnswer.language === 'tg' && !caps?.tts_languages.includes('tg'))} aria-label="РџРѕРІС‚РѕСЂРёС‚СЊ РѕС‚РІРµС‚"><RotateCcw size={17}/></button><button type="button" className="pilot-control" onClick={clearHistory} aria-label="РћС‡РёСЃС‚РёС‚СЊ РґРёР°Р»РѕРі"><Trash2 size={17}/></button></div>
        </form>
        <p className="pilot-privacy">РњРёРєСЂРѕС„РѕРЅ РІРєР»СЋС‡Р°РµС‚СЃСЏ РїРѕ РЅР°Р¶Р°С‚РёСЋ. Р Р°СЃРїРѕР·РЅР°РЅРЅР°СЏ С„СЂР°Р·Р° Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РѕС‚РїСЂР°РІР»СЏРµС‚СЃСЏ РєР°Рє РІРѕРїСЂРѕСЃ. РђСѓРґРёРѕ РѕР±СЂР°Р±Р°С‚С‹РІР°РµС‚СЃСЏ Р»РѕРєР°Р»СЊРЅРѕ Рё РЅРµ СЃРѕС…СЂР°РЅСЏРµС‚СЃСЏ.</p>
      </div>
    </Dialog> : null}
  </></Localized>;
});
