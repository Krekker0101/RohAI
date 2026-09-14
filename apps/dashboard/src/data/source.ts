export type DataSourceMode = 'demo' | 'real';

const STORAGE_KEY = 'smart-traffic-data-source';

function initialMode(): DataSourceMode {
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === 'real' || stored === 'demo' ? stored : 'demo';
}

let mode: DataSourceMode = initialMode();

export function getDataSourceMode(): DataSourceMode {
  return mode;
}

export function setDataSourceMode(value: DataSourceMode): DataSourceMode {
  mode = value;
  window.localStorage.setItem(STORAGE_KEY, value);
  return mode;
}

export function runtimePath(path: string): string {
  if (mode !== 'demo') return path;
  if (path === '/api/v1/system') return '/api/v1/demo/system';
  if (path === '/health/ready') return '/health/demo-ready';
  if (path === '/api/v1/state') return '/api/v1/demo/state';
  if (path === '/ws/telemetry') return '/ws/demo/telemetry';
  if (path === '/api/v1/control/policy') return '/api/v1/demo/control/policy';
  if (path === '/api/v1/emergency') return '/api/v1/demo/emergency';
  if (path === '/api/v1/simulation/compare') return '/api/v1/demo/simulation/compare';
  if (path === '/api/v1/vision/frame.jpg') return '/api/v1/demo/vision/frame.svg';
  return path;
}
