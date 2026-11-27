import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import { tanstackRouter } from '@tanstack/router-plugin/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * Vite and Vitest configuration of the frontend.
 *
 * The router plugin generates the route tree from the route files, and `/api` is proxied to the
 * backend, so the browser sees one origin and the session and CSRF cookies work as they do in production.
 *
 * The tests run in two projects. A test of a component (`*.test.tsx`) gets the DOM of jsdom, and a test of logic
 * (`*.test.ts`) runs in Node, since starting jsdom for every file costs most of the run. A file that differs from
 * its extension says so on its first line with `// @vitest-environment jsdom` or `node`.
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
    projects: [
      {
        extends: true,
        test: { name: 'logic', environment: 'node', include: ['src/**/*.test.ts'] },
      },
      {
        extends: true,
        test: { name: 'components', environment: 'jsdom', include: ['src/**/*.test.tsx'] },
      },
    ],
  },
});
