import { request, requestRaw } from '../../data/api';
export type PilotPage = 'overview' | 'video' | 'analytics' | 'comparison' | 'calibration' | 'intelligence' | 'operations';
export type PilotLanguage = 'ru' | 'en' | 'tg';
export type PilotLevel = 'auto' | 'simple' | 'standard' | 'technical';
export interface Capabilities { chat_available: boolean; stt_available: boolean; tts_languages: string[]; languages: PilotLanguage[]; knowledge_records: number; provider: string; read_only: boolean; generation_status?: 'experimental' | 'local' }
export interface Answer {
  conversation_id: string; response_id: string; answer: string; outcome: 'grounded' | 'unknown' | 'out_of_scope'; language: PilotLanguage;
  sources: { id: string; title: string; status: string; files: { path: string; sha256: string }[] }[];
}
export async function ask(question: string, conversation: string | null, page: PilotPage, mode: string, level: PilotLevel, language: PilotLanguage, signal: AbortSignal): Promise<Answer> {
  const answer = await request<Answer | undefined>('/api/v1/pilot/chat', 'POST', { question, conversation_id: conversation, page, mode, level, language }, signal);
  if (!answer) throw new Error('PILOT_REQUEST_CANCELLED');
  return answer;
}
export async function transcribe(audio: Blob, language: PilotLanguage, signal: AbortSignal): Promise<string> {
  const response = await requestRaw(`/api/v1/pilot/transcribe?language=${language}`, { method: 'POST', body: audio, headers: { 'Content-Type': 'audio/wav' }, signal });
  const result = await response.json() as { text: string }; return result.text;
}
export function speech(answer: Answer, signal: AbortSignal) {
  return requestRaw('/api/v1/pilot/speech', { method: 'POST', body: JSON.stringify({ conversation_id: answer.conversation_id, response_id: answer.response_id }), headers: { 'Content-Type': 'application/json' }, signal });
}
