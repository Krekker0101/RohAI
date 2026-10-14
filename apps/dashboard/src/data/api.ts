import { backendUrl } from './backend';
import { runtimePath } from './source';

let token = '';
export const setOperatorToken = (value: string) => { token = value.trim(); };

export async function requestBlob(path: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(backendUrl(path), {
    signal, cache: 'no-store', credentials: 'omit',
    headers: token ? { 'X-Operator-Token': token } : {},
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.blob();
}

export function uploadVideo<T>(file: File, onProgress: (percent: number) => void, signal: AbortSignal, profile: 'standard' | 'aerial' | 'aerial_precise' = 'standard', roads = false): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal.removeEventListener('abort', abort);
    xhr.open('POST', backendUrl(`/api/v1/video/jobs?filename=${encodeURIComponent(file.name)}&profile=${profile}&roads=${roads}`));
    xhr.timeout = 300000;
    xhr.setRequestHeader('Content-Type', file.type.startsWith('video/') ? file.type : 'application/octet-stream');
    if (token) xhr.setRequestHeader('X-Operator-Token', token);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onload = () => {
      cleanup();
      try {
        const result = JSON.parse(xhr.responseText) as T & { detail?: string };
        if (xhr.status < 200 || xhr.status >= 300) reject(new Error(result.detail ?? `HTTP ${xhr.status}`));
        else resolve(result);
      } catch { reject(new Error('Backend РІРµСЂРЅСѓР» РЅРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РѕС‚РІРµС‚')); }
    };
    xhr.onerror = () => { cleanup(); reject(new Error('РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РіСЂСѓР·РёС‚СЊ РІРёРґРµРѕ. РџСЂРѕРІРµСЂСЊС‚Рµ РїРѕРґРєР»СЋС‡РµРЅРёРµ.')); };
    xhr.ontimeout = () => { cleanup(); reject(new Error('Р’СЂРµРјСЏ Р·Р°РіСЂСѓР·РєРё РёСЃС‚РµРєР»Рѕ')); };
    xhr.onabort = () => { cleanup(); reject(new DOMException('Upload cancelled', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) { cleanup(); reject(new DOMException('Upload cancelled', 'AbortError')); return; }
    xhr.send(file);
  });
}

async function errorMessage(response: Response): Promise<string> {
  const type = response.headers.get('content-type') ?? '';
  if (type.includes('application/json')) {
    const data: { detail?: unknown } = await response.json().catch(() => ({}));
    if (typeof data.detail === 'string') return data.detail;
    if (data.detail !== undefined) return JSON.stringify(data.detail);
  }
  const text = await response.text().catch(() => '');
  return text.trim() || response.statusText || 'Р—Р°РїСЂРѕСЃ РЅРµ РІС‹РїРѕР»РЅРµРЅ';
}

export async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(backendUrl(runtimePath(path)), {
    method,
    cache: 'no-store',
    credentials: 'omit',
    signal: signal ?? AbortSignal.timeout(method === 'POST' ? 60000 : 10000),
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { 'X-Operator-Token': token } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await errorMessage(response);
    throw new Error(response.status === 401
      ? 'РќСѓР¶РµРЅ С‚РѕРєРµРЅ РѕРїРµСЂР°С‚РѕСЂР°. Р”РѕР±Р°РІСЊС‚Рµ РµРіРѕ РІ РЅР°СЃС‚СЂРѕР№РєР°С… РїРѕРґРєР»СЋС‡РµРЅРёСЏ.'
      : `${response.status}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get('content-type') ?? '';
  if (!type.includes('application/json')) {
    throw new Error(`Backend РІРµСЂРЅСѓР» РЅРµРѕР¶РёРґР°РЅРЅС‹Р№ Content-Type: ${type || 'РЅРµ СѓРєР°Р·Р°РЅ'}`);
  }
  return response.json() as Promise<T>;
}

export function downloadJSON(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}


/** Authenticated raw response for local voice and bounded streaming APIs. */
export async function requestRaw(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set('X-Operator-Token', token);
  const response = await fetch(backendUrl(path), { ...init, headers, credentials: 'omit', cache: 'no-store' });
  if (!response.ok) throw new Error(`${response.status}: ${await errorMessage(response)}`);
  return response;
}
