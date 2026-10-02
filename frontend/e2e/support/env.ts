import path from 'node:path';

/**
 * Where the end-to-end run keeps its server: the port, the database and the log the server writes the mail to.
 *
 * The Playwright configuration starts the server with these, and the scenarios read the log, because without an
 * SMTP server the confirmation mail is only a log line.
 */

// Two runs at once, such as in two worktrees, each take a port of their own through the environment
export const SERVER_PORT = Number(process.env.BOOKREVIVER_E2E_PORT ?? 8765);
export const BASE_URL = `http://127.0.0.1:${SERVER_PORT}`;
// BOOKREVIVER_E2E_DATA_DIR moves the run elsewhere, one folder per port so that parallel runs keep apart
const DATA_ROOT = process.env.BOOKREVIVER_E2E_DATA_DIR;
export const RUN_DIR =
  DATA_ROOT === undefined
    ? path.resolve(import.meta.dirname, '..', '..', '.e2e-data')
    : path.join(DATA_ROOT, String(SERVER_PORT));
export const SERVER_LOG = path.join(RUN_DIR, 'server.log');
