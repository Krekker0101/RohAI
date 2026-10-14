/** Local PCM capture/VAD and streamed playback. Never uses cloud SpeechRecognition. */
export function rms(samples: Float32Array): number {
  let sum = 0;
  for (const value of samples) sum += value * value;
  return Math.sqrt(sum / Math.max(1, samples.length));
}
export function pcmWave(samples: Float32Array, sampleRate = 16000): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, index) => view.setInt16(44 + index * 2, Math.round(Math.max(-1, Math.min(1, value)) * 32767), true));
  return new Blob([buffer], { type: 'audio/wav' });
}

/** Boost quiet laptop recordings without clipping or amplifying silent input. */
export function normalizeRecording(samples: Float32Array): Float32Array {
  let peak = 0;
  for (const value of samples) peak = Math.max(peak, Math.abs(value));
  if (peak < 0.001 || rms(samples) < 0.0003) throw new Error('Микрофон не записал голос. Проверьте выбранный микрофон и его громкость в Windows.');
  const gain = Math.min(12, 0.85 / peak);
  return samples.map(value => value * gain);
}

/** Detect a pause relative to the voice level, even with steady fan noise. */
export class SpeechPauseDetector {
  private peak = 0;
  private spoken = 0;
  private silence = 0;
  update(energy: number, seconds: number): boolean {
    this.peak = Math.max(energy, this.peak * 0.999);
    if (energy > Math.max(0.003, this.peak * 0.25)) {
      this.spoken += seconds; this.silence = 0;
    } else this.silence += seconds;
    return this.spoken > 0.2 && this.silence > 1.6;
  }
}

export class MicrophoneManager {
  private context?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private chunks: Float32Array[] = [];
  private samples = 0;
  private spokenSamples = 0;
  private silenceSamples = 0;
  private epoch = 0;
  private finishing = false;
  private deadline?: ReturnType<typeof setTimeout>;
  private watchdog?: ReturnType<typeof setInterval>;
  private complete?: (wave: Blob) => void;
  private fail?: (error: Error) => void;

  async start(level: (value: number) => void, complete: (wave: Blob) => void, fail: (error: Error) => void): Promise<void> {
    this.stop(); const epoch = this.epoch;
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) throw new Error('Для микрофона нужен современный браузер и HTTPS либо localhost. Текстовый диалог доступен.');
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (this.epoch !== epoch) { stream.getTracks().forEach(track => track.stop()); return; }
    this.stream = stream; this.context = new AudioContext(); await this.context.resume();
    try { await this.context.audioWorklet.addModule(`${import.meta.env.BASE_URL}pilot-recorder.js`); }
    catch (error) { this.stop(); throw error; }
    if (this.epoch !== epoch || !this.context) return;
    this.complete = complete; this.fail = fail; this.finishing = false;
    const context = this.context;
    this.node = new AudioWorkletNode(context, 'pilot-recorder');
    const pauses = new SpeechPauseDetector();
    let lastBlock = performance.now();
    // Wall time also bounds capture when a suspended audio graph sends no blocks.
    this.deadline = setTimeout(() => { if (epoch === this.epoch) void this.finish(); }, 19800);
    this.watchdog = setInterval(() => {
      if (epoch === this.epoch && performance.now() - lastBlock > 3000) {
        const callback = this.fail; this.stop();
        callback?.(new Error('Микрофон не передаёт звук. Проверьте устройство ввода в Windows и разрешение браузера.'));
      }
    }, 1000);
    context.createMediaStreamSource(stream).connect(this.node); this.node.connect(context.destination);
    this.node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      if (this.epoch !== epoch || this.finishing) return;
      const block = event.data;
      lastBlock = performance.now();
      this.chunks.push(block); this.samples += block.length;
      const energy = rms(block); level(Math.min(1, energy * 8));
      if (energy > 0.003) { this.spokenSamples += block.length; this.silenceSamples = 0; }
      else this.silenceSamples += block.length;
      if (this.samples >= context.sampleRate * 19.8 || pauses.update(energy, block.length / context.sampleRate)) void this.finish();
      else if (this.samples > context.sampleRate * 8 && !this.spokenSamples) { const callback = this.fail; this.stop(); callback?.(new Error('Не услышал речь. Попробуйте ещё раз или напишите вопрос.')); }
    };
  }

  async finish(): Promise<void> {
    if (!this.context || this.finishing) return;
    this.finishing = true;
    const epoch = this.epoch;
    const chunks = this.chunks; const total = this.samples; const rate = this.context.sampleRate;
    const callback = this.complete; const fail = this.fail;
    this.release(); this.chunks = []; this.samples = this.spokenSamples = this.silenceSamples = 0;
    if (!callback) return;
    if (total < rate * 0.2) { fail?.(new Error('Запись слишком короткая. Нажмите микрофон и произнесите вопрос.')); return; }
    try {
      const samples = new Float32Array(total); let offset = 0;
      for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
      const offline = new OfflineAudioContext(1, Math.max(1, Math.round(total * 16000 / rate)), 16000);
      const buffer = offline.createBuffer(1, total, rate); buffer.copyToChannel(samples, 0);
      const source = offline.createBufferSource(); source.buffer = buffer; source.connect(offline.destination); source.start();
      const rendered = await offline.startRendering();
      if (epoch === this.epoch) callback(pcmWave(normalizeRecording(rendered.getChannelData(0))));
    } catch (error) { if (epoch === this.epoch) fail?.(error instanceof Error ? error : new Error('Ошибка записи голоса')); }
  }

  private release(): void {
    clearTimeout(this.deadline); clearInterval(this.watchdog); this.deadline = this.watchdog = undefined;
    if (this.node) { this.node.port.onmessage = null; this.node.disconnect(); this.node = undefined; }
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
    if (this.context) void this.context.close(); this.context = undefined;
  }
  stop(): void { this.epoch++; this.release(); this.chunks = []; this.samples = this.spokenSamples = this.silenceSamples = 0; this.complete = this.fail = undefined; this.finishing = false; }
}

export class AudioPlaybackController {
  private context?: AudioContext;
  private analyser?: AnalyserNode;
  private sources = new Set<AudioBufferSourceNode>();
  private next = 0;
  private frame = 0;
  private epoch = 0;
  private reading = false;
  constructor(private level: (value: number) => void, private started: () => void, private ended: () => void) {}
  async prepare(): Promise<void> {
    if (!this.context || this.context.state === 'closed') {
      this.context = new AudioContext(); this.analyser = this.context.createAnalyser(); this.analyser.fftSize = 512;
      this.analyser.connect(this.context.destination);
    }
    await this.context.resume();
  }
  private pulse(): void {
    if (!this.analyser || !this.sources.size) { this.frame = 0; this.level(0); return; }
    const values = new Float32Array(this.analyser.fftSize); this.analyser.getFloatTimeDomainData(values);
    this.level(Math.min(1, rms(values) * 4));
    this.frame = requestAnimationFrame(() => this.pulse());
  }
  private append(encoded: string, sampleRate: number, epoch: number): void {
    if (!this.context || !this.analyser || epoch !== this.epoch) return;
    if (!Number.isInteger(sampleRate) || sampleRate < 8000 || sampleRate > 48000 || encoded.length > 4_000_000) throw new Error('Некорректное аудио');
    const raw = atob(encoded); const bytes = Uint8Array.from(raw, value => value.charCodeAt(0));
    if (bytes.length % 2) throw new Error('Некорректное аудио');
    const pcm = new DataView(bytes.buffer); const samples = new Float32Array(bytes.length / 2);
    samples.forEach((_, index) => { samples[index] = pcm.getInt16(index * 2, true) / 32768; });
    const buffer = this.context.createBuffer(1, samples.length, sampleRate); buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource(); source.buffer = buffer; source.connect(this.analyser);
    const when = Math.max(this.context.currentTime + 0.025, this.next); this.next = when + buffer.duration;
    source.onended = () => { source.disconnect(); this.sources.delete(source); if (epoch === this.epoch && !this.reading && !this.sources.size) { this.level(0); this.ended(); } };
    this.sources.add(source); source.start(when); this.started(); if (!this.frame) this.pulse();
  }
  async consume(response: Response, signal: AbortSignal): Promise<void> {
    if (!response.body) throw new Error('Аудиопоток недоступен');
    const epoch = this.epoch; const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = ''; this.reading = true;
    const abort = () => { void reader.cancel(); };
    signal.addEventListener('abort', abort, { once: true });
    let chunks = 0;
    try {
      while (!signal.aborted && epoch === this.epoch) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        if (pending.length > 4_000_000) throw new Error('Слишком большой аудиофрагмент');
        let index: number;
        while ((index = pending.indexOf('\n')) >= 0) {
          const event = JSON.parse(pending.slice(0, index)) as { audio?: string; sample_rate?: number; error?: string };
          pending = pending.slice(index + 1);
          if (event.error) throw new Error('Локальный синтез речи недоступен. Текст ответа сохранён.');
          if (event.audio && event.sample_rate) { this.append(event.audio, event.sample_rate, epoch); chunks++; }
        }
      }
      if (!signal.aborted && !chunks) throw new Error('Не получено аудио. Текст ответа доступен.');
    } finally { signal.removeEventListener('abort', abort); reader.releaseLock(); if (epoch === this.epoch) this.reading = false; if (!this.sources.size && epoch === this.epoch) { this.level(0); this.ended(); } }
  }
  stop(): void {
    this.epoch++; this.reading = false;
    for (const source of this.sources) { source.onended = null; source.stop(); source.disconnect(); }
    this.sources.clear(); this.next = 0; cancelAnimationFrame(this.frame); this.frame = 0; this.level(0);
  }
  dispose(): void { this.stop(); if (this.context) void this.context.close(); this.context = undefined; this.analyser = undefined; }
}
