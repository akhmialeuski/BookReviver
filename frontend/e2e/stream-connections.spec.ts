import { rm } from 'node:fs/promises';
import path from 'node:path';
import { type Browser, type BrowserContext, expect, test } from '@playwright/test';
import {
  createBook,
  openOrderStage,
  openProjectId,
  readPlace,
  registerAndSignIn,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import { pageIds } from './support/page-work';

/**
 * Many open tabs of one book do not hinder the work in another: every open tab holds a server-sent event stream, and a
 * stream that kept a database connection for as long as its tab stayed open would leave the saves of the reading place
 * and of a page waiting for a connection of the pool until the pool gave up.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 180_000;
const STREAMS_TIMEOUT_MS = 120_000;
// The pool of the server gives 15 connections (5 and an overflow of 10), so 20 tabs are more than it could ever lend
const TABS = 20;
// A save that waits for a connection of the pool fails after the 30 s of its timeout, so one that arrives sooner did not wait
const SAVE_TIMEOUT_MS = 15_000;
const NOTE = 'Seen with twenty tabs open';

/**
 * Open the book in a browser context of its own for each tab, signed in as the reader, and wait until the server has
 * answered the request of its event stream.
 *
 * Chromium allows six HTTP/1.1 connections to a host for a whole profile, so tabs of one context beyond the sixth would
 * wait in the browser and never reach the server. Each context has a network stack of its own and holds its stream.
 *
 * The tabs open the description of the book, which holds the stream of the book as every screen of the book does and
 * reads little else. Twenty stage screens opened at the same moment would ask the one server process for some two
 * thousand pictures and rows at once and slow every scenario running beside this one, which measures held connections,
 * not a burst of reads.
 *
 * @param contexts Receives every context as it is made, so the caller can close them even when a tab fails to open.
 * @param storageState The cookies of the signed-in reader, which every context starts with.
 */
async function openTabs(
  browser: Browser,
  contexts: BrowserContext[],
  storageState: Awaited<ReturnType<BrowserContext['storageState']>>,
  projectId: string,
): Promise<void> {
  const eventsPath = `/api/v1/projects/${projectId}/events`;
  await Promise.all(
    Array.from({ length: TABS }, async () => {
      const context = await browser.newContext({ storageState });
      contexts.push(context);
      const tab = await context.newPage();
      // The response arrives with the headers of the stream, after the server has checked the owner of the book
      const answered = tab.waitForResponse((response) => response.url().endsWith(eventsPath), {
        timeout: STREAMS_TIMEOUT_MS,
      });
      await tab.goto(`/projects/${projectId}/about`);
      const response = await answered;
      expect(response.status()).toBe(200);
      expect(response.headers()['content-type']).toContain('text/event-stream');
    }),
  );
}

test('twenty open tabs of a book do not hinder saving the reading place and editing a page', async ({
  page,
  browser,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const contexts: BrowserContext[] = [];

  try {
    await registerAndSignIn(page);
    await createBook(page, 'A book in many tabs');
    await uploadFolder(page, folder, PAGES);
    const projectId = openProjectId(page);
    await page.goto(`/projects/${projectId}/stages/geometry`);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');

    await test.step(`the book is open in ${TABS} tabs, each with its event stream`, async () => {
      await openTabs(browser, contexts, await page.context().storageState(), projectId);
      expect(contexts).toHaveLength(TABS);
    });

    await test.step('the reading place is saved in the normal time', async () => {
      const second = (await pageIds(page))[1] ?? '';
      expect(second).not.toBe('');
      const saved = page.waitForResponse(
        (response) =>
          response.request().method() === 'PUT' &&
          response.url().endsWith(`/api/v1/projects/${projectId}/place`) &&
          (response.request().postData() ?? '').includes(second),
        { timeout: SAVE_TIMEOUT_MS },
      );
      await page.getByTestId('strip-page').nth(1).click();
      await expect(page.getByTestId('canvas-caption')).toContainText(`2 of ${PAGES}`);
      expect((await saved).ok()).toBe(true);
      expect((await readPlace(page, projectId)).place?.page_id).toBe(second);
    });

    await test.step('a page is edited in the normal time', async () => {
      await openOrderStage(page);
      await page.getByTestId('order-tile').first().click();
      await page.getByLabel('Notes').fill(NOTE);
      const saved = page.waitForResponse(
        (response) =>
          response.request().method() === 'PATCH' &&
          (response.request().postData() ?? '').includes(NOTE),
        { timeout: SAVE_TIMEOUT_MS },
      );
      await page.getByLabel('Notes').blur();
      expect((await saved).ok()).toBe(true);
    });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    await rm(path.dirname(folder), { recursive: true, force: true });
  }
});
