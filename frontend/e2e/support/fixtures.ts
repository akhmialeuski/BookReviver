import { access } from 'node:fs/promises';
import path from 'node:path';
import { test as base } from '@playwright/test';
import { registerAndSignIn } from './account';
import { BASE_URL, RUN_DIR } from './env';

/**
 * The `test` of the scenarios that work in books of their own and need no account of their own: one account for each
 * Playwright worker, which the worker registers and signs in once and every browser context of its scenarios starts
 * signed in as.
 *
 * This is the approach "one account per parallel worker" of https://playwright.dev/docs/auth. The scenarios of a worker
 * run one after another, so they never meet in the books of the account, and each creates a book with a title of its
 * own. A scenario that is about the account itself, such as the registration, the sign-in, the sign-out and the
 * password, or that reads the whole library, the profiles or the settings of the account, imports `test` from
 * `@playwright/test` instead and registers the account it needs with `registerAndSignIn`.
 *
 * Each registration and each sign-in hashes a password, which costs the server tens of milliseconds of one core, so
 * registering once for a worker spares the run about a hundred hashes.
 */

const WORKER_ACCOUNT_TIMEOUT_MS = 60_000;

export const test = base.extend<Record<never, never>, { workerStorageState: string }>({
  // Every context of the worker starts with the cookies of the account of the worker
  storageState: ({ workerStorageState }, use) => use(workerStorageState),

  workerStorageState: [
    async ({ browser }, use, workerInfo) => {
      // The parallel index names the worker slot and survives a restart of the worker after a failed scenario, so the
      // restarted worker finds the account of its slot and uses it again. The file lives in the data folder of the
      // run, which the web server empties at its start, so it never outlives the database that holds the account.
      const fileName = path.join(RUN_DIR, 'accounts', `${workerInfo.parallelIndex}.json`);
      const registered = await access(fileName).then(
        () => true,
        () => false,
      );
      if (!registered) {
        // A browser of the worker has no options of the project, so the context is given the address of the server
        const page = await browser.newPage({ baseURL: BASE_URL });
        await registerAndSignIn(page);
        await page.context().storageState({ path: fileName });
        await page.close();
      }
      await use(fileName);
    },
    { scope: 'worker', timeout: WORKER_ACCOUNT_TIMEOUT_MS },
  ],
});
