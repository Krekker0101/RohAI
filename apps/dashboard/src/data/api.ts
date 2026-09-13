let token = '';
export const setOperatorToken = (value: string) => { token = value.trim(); };
export async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method, cache: 'no-store', signal: signal ?? AbortSignal.timeout(method === 'POST' ? 60000 : 10000),
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(token ? { 'X-Operator-Token': token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data: { detail?: unknown } = await response.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? 'Запрос не выполнен');
    throw new Error(response.status === 401 ? 'Нужен токен оператора. Добавьте его в настройках подключения.' : `${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}
export function downloadJSON(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
