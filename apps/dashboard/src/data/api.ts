import { backendUrl } from './backend';
import { runtimePath } from './source';

let token = '';
export const setOperatorToken = (value: string) => { token = value.trim(); };

async function errorMessage(response: Response): Promise<string> {
  const type = response.headers.get('content-type') ?? '';
  if (type.includes('application/json')) {
    const data: { detail?: unknown } = await response.json().catch(() => ({}));
    if (typeof data.detail === 'string') return data.detail;
    if (data.detail !== undefined) return JSON.stringify(data.detail);
  }
  const text = await response.text().catch(() => '');
  return text.trim() || response.statusText || 'Запрос не выполнен';
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
      ? 'Нужен токен оператора. Добавьте его в настройках подключения.'
      : `${response.status}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get('content-type') ?? '';
  if (!type.includes('application/json')) {
    throw new Error(`Backend вернул неожиданный Content-Type: ${type || 'не указан'}`);
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
