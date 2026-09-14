const STORAGE_KEY = 'smart-traffic-backend-url';

function normalizeBackend(value: string): URL {
  const trimmed = value.trim();
  const url = new URL(trimmed || window.location.origin, window.location.origin);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('Backend URL должен начинаться с http:// или https://');
  }
  url.hash = '';
  url.search = '';
  if (!url.pathname.endsWith('/')) url.pathname += '/';
  return url;
}

function initialBackend(): URL {
  const env = import.meta.env.VITE_API_BASE_URL?.trim();
  const stored = window.localStorage.getItem(STORAGE_KEY)?.trim();
  try {
    return normalizeBackend(stored || env || window.location.origin);
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return normalizeBackend(env || window.location.origin);
  }
}

let backend = initialBackend();

export function backendUrl(path = ''): string {
  return new URL(path.replace(/^\/+/, ''), backend).toString();
}

export function webSocketUrl(path: string): string {
  const url = new URL(path.replace(/^\/+/, ''), backend);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export function getBackendBaseUrl(): string {
  return backend.toString().replace(/\/$/, '');
}

export function setBackendBaseUrl(value: string): string {
  backend = normalizeBackend(value);
  const normalized = getBackendBaseUrl();
  if (backend.origin === window.location.origin && backend.pathname === '/') {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, normalized);
  }
  return normalized;
}

export function resetBackendBaseUrl(): string {
  window.localStorage.removeItem(STORAGE_KEY);
  backend = normalizeBackend(import.meta.env.VITE_API_BASE_URL?.trim() || window.location.origin);
  return getBackendBaseUrl();
}
