import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * Vite and Vitest configuration of the frontend.
 *
 * The router plugin turns the files of `src/routes/` into `src/routeTree.gen.ts`, and `/api` is proxied to the
 * backend, so the browser sees one origin and the session and CSRF cookies work as they do in production.
 */

const BACKEND_URL = process.env.BOOKREVIVER_BACKEND_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [tanstackRouter({ target: 'react', autoCodeSplitting: true }), react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: { '/api': { target: BACKEND_URL, changeOrigin: false } },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
