import { defineConfig, devices } from '@playwright/test';
import { BASE_URL, RUN_DIR, SERVER_LOG, SERVER_PORT } from './e2e/support/env';

/**
 * Playwright configuration of the end-to-end scenarios.
 *
 * The scenarios run against the real thing: the built frontend served by the backend through `app.frontend()`, on
 * a fresh database in `.e2e-data`, with the mail written to its log and a fake provider offered in place of Google. `npm run e2e` builds the frontend first.
 * Where the Chromium that Playwright expects is not installed, `BOOKREVIVER_E2E_CHROMIUM` names another one.
 */

// uvicorn closes an idle connection after 5 s by default, which races the keep-alive of the HTTP client of Playwright
// (a request reuses a socket the server is closing and fails with "socket hang up"), so the server keeps it longer
const SERVER_KEEP_ALIVE_S = 75;

const SERVER_COMMAND = [
  `rm -rf ${RUN_DIR}`,
  `mkdir -p ${RUN_DIR}`,
  'uv run bookreviver-migrate upgrade head --no-prompt',
  `uv run uvicorn tests.helpers.e2e_app:app --port ${SERVER_PORT} --timeout-keep-alive ${SERVER_KEEP_ALIVE_S} 2>&1 | tee ${SERVER_LOG}`,
].join(' && ');

// Every scenario registers an account of its own, so scenarios can share the one server and its database
const DEFAULT_WORKERS = 3;
const TEST_TIMEOUT_MS = 90_000;
const EXPECT_TIMEOUT_MS = 15_000;
const WORKERS = Number(process.env.BOOKREVIVER_E2E_WORKERS ?? DEFAULT_WORKERS);

export default defineConfig({
  testDir: 'e2e',
  // Scenario files run side by side, and the steps inside one file in turn
  fullyParallel: false,
  workers: WORKERS,
  // The scenarios share one server process, which answers each of them more slowly while the others work
  timeout: TEST_TIMEOUT_MS,
  expect: { timeout: EXPECT_TIMEOUT_MS },
  retries: 0,
  reporter: 'list',
  use: { baseURL: BASE_URL, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
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
      BOOKREVIVER_PUBLIC_URL: BASE_URL,
      BOOKREVIVER_DATA_DIR: `${RUN_DIR}/data`,
      BOOKREVIVER_FRONTEND_DIR: 'frontend/dist',
    },
  },
});
