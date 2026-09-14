import { defineConfig } from 'vitest/config';
import { loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backend = env.VITE_DEV_BACKEND_URL || 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    base: env.VITE_BASE_PATH || '/dashboard/',
    server: {
      host: '127.0.0.1',
      proxy: {
        '/api': { target: backend, changeOrigin: true },
        '/health': { target: backend, changeOrigin: true },
        '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
      },
    },
    test: { include: ['src/**/*.test.ts'], environment: 'node' },
  };
});
