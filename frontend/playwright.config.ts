import { defineConfig, devices } from '@playwright/test';
import { BASE_URL, RUN_DIR, SERVER_LOG, SERVER_PORT } from './e2e/support/env';

/**
 * Playwright configuration of the end-to-end scenarios.
 *
 * The scenarios run against the real thing: the built frontend served by the backend through `app.frontend()`, on
 * a fresh database in `.e2e-data`, with the mail written to its log. `npm run e2e` builds the frontend first.
 * Where the Chromium that Playwright expects is not installed, `BOOKREVIVER_E2E_CHROMIUM` names another one.
 */

const SERVER_COMMAND = [
  `rm -rf ${RUN_DIR}`,
  `mkdir -p ${RUN_DIR}`,
  'uv run bookreviver-migrate upgrade head --no-prompt',
  `uv run fastapi run --port ${SERVER_PORT} 2>&1 | tee ${SERVER_LOG}`,
].join(' && ');

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: { baseURL: BASE_URL, trace: 'retain-on-failure' },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: { executablePath: process.env.BOOKREVIVER_E2E_CHROMIUM },
      },
    },
  ],
  webServer: {
    command: SERVER_COMMAND,
    cwd: '..',
    url: BASE_URL,
    timeout: 120_000,
    reuseExistingServer: false,
    env: {
      BOOKREVIVER_AUTH__SECRET: 'end-to-end-secret-that-is-long-enough-to-sign-with',
      BOOKREVIVER_AUTH__COOKIE_SECURE: 'false',
      BOOKREVIVER_DATA_DIR: `${RUN_DIR}/data`,
      BOOKREVIVER_FRONTEND_DIR: 'frontend/dist',
    },
  },
});
