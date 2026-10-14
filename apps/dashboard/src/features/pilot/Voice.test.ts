import { describe, expect, it } from 'vitest';
import { normalizeRecording, pcmWave, rms, SpeechPauseDetector } from './Voice';

describe('microphone recordings', () => {
  it('ends a spoken phrase despite steady background noise above the old threshold', () => {
    const detector = new SpeechPauseDetector();
    for (let i = 0; i < 10; i++) expect(detector.update(0.04, 0.1)).toBe(false);
    for (let i = 0; i < 15; i++) expect(detector.update(0.006, 0.1)).toBe(false);
    expect(detector.update(0.006, 0.2)).toBe(true);
  });
  it('does not cut a phrase during a short pause', () => {
    const detector = new SpeechPauseDetector();
    for (let i = 0; i < 5; i++) detector.update(0.008, 0.1);
    expect(detector.update(0, 1)).toBe(false);
    expect(detector.update(0.008, 0.1)).toBe(false);
    expect(detector.update(0, 1)).toBe(false);
  });
  it('boosts quiet speech while preserving its waveform and source', () => {
    const input = Float32Array.from({ length: 1600 }, (_, i) => Math.sin(i / 10) * 0.004);
    const result = normalizeRecording(input);
    expect(rms(result)).toBeCloseTo(rms(input) * 12, 5);
    expect(input[10]).toBeCloseTo(Math.sin(1) * 0.004, 7);
  });
  it('rejects silent and nearly silent recordings instead of hallucinating words', () => {
    expect(() => normalizeRecording(new Float32Array(1600))).toThrow('Микрофон');
    expect(() => normalizeRecording(new Float32Array(1600).fill(0.0001))).toThrow('Микрофон');
  });
  it('keeps loud speech below clipping and exports mono 16 kHz PCM', async () => {
    const normalized = normalizeRecording(new Float32Array([1, -1, 0.5]));
    expect(Math.max(...normalized.map(Math.abs))).toBeLessThan(1);
    const bytes = new DataView(await pcmWave(normalized).arrayBuffer());
    expect(bytes.getUint32(24, true)).toBe(16000);
    expect(bytes.getUint16(22, true)).toBe(1);
    expect(bytes.getUint32(40, true)).toBe(6);
    expect(bytes.getInt16(44, true)).toBeCloseTo(27852, 0);
  });
});
