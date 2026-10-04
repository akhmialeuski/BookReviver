import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../src/shared/http/csrf';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';

/**
 * The history of a step on a page: a setting changed in the panel and an edit saved by the editor are listed with what
 * they changed, the button takes the newest change back, Ctrl+Z takes back the one before it, and the history keeps the
 * undos beside the changes they took back.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const DESKEW = 'geometry.deskew';
const DESKEW_TITLE = 'Deskew';
const FIRST_PAGE = 0;
const ANGLE_OF_THE_EDIT = 1.5;
const SLANT_OF_THE_PAGE = '3';
const CHANGES_WRITTEN = 2;
const ROWS_AFTER_ONE_UNDO = 3;
const ROWS_AFTER_TWO_UNDOS = 4;

// Tall enough for the pictures of the key states to show the open step with its settings and its history
test.use({ viewport: { width: 1280, height: 1100 } });

interface HistoryItem {
  layer: string;
  source: string;
  undone: boolean;
}

/** Read the identifier of the first page of the open book. */
async function firstPageId(page: Page): Promise<string> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  const found = items.find((item) => item.position === FIRST_PAGE);
  if (found === undefined) {
    throw new Error('The book has no first page.');
  }
  return found.id;
}

/** Save the rotation edit of a step as the editor does, which writes the manual layer of the step to the history. */
async function saveRotation(page: Page, pageId: string, stepId: string): Promise<void> {
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/edits/geometry/${stepId}`,
    {
      headers: { [CSRF_HEADER_NAME]: token },
      multipart: { kind: 'rotation', geometry: JSON.stringify({ degrees: ANGLE_OF_THE_EDIT }) },
    },
  );
  expect(response.ok()).toBe(true);
}

/** Read the history of a step on a page from the server, the newest change first. */
async function readHistory(page: Page, pageId: string, stepId: string): Promise<HistoryItem[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/history/geometry/${stepId}?size=100`,
  );
  return ((await response.json()) as { items: HistoryItem[] }).items;
}

test('a reader sees what changed on a page, takes the last change back with the button and Ctrl+Z, and finds the undos in the history', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  // The settings of the page and its history are in the panel of the open step
  const step = page.getByTestId('step-panel');
  const history = step.getByTestId('page-history');
  const rows = history.getByTestId('page-history-row');
  const settings = step.getByTestId('page-settings');
  let pageId = '';
  let stepId = '';

  await test.step('a book opens on the Geometry stage, and its first page has an edit saved by hand', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with a history');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(page.getByTestId('bar-step').first()).toBeVisible();
    pageId = await firstPageId(page);
    [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    expect(stepId).not.toBe('');
    await saveRotation(page, pageId, stepId);
  });

  await test.step('the open step lists the edit, and a setting changed for this page joins it above', async () => {
    await page.getByTestId('bar-step').filter({ hasText: DESKEW_TITLE }).click();
    await expect(step).toBeVisible();
    await expect(history).toBeVisible();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('Set by hand');
    await settings.getByTestId('page-settings-edit').click();
    await settings.locator('form input[type="number"]').first().fill(SLANT_OF_THE_PAGE);
    await expect(rows).toHaveCount(CHANGES_WRITTEN);
    await expect(rows.first()).toContainText('Settings of the page');
    await expect(rows.first()).toContainText(SLANT_OF_THE_PAGE);
    await expect(settings.getByTestId('page-settings-list')).toBeVisible();
    await snap(page, 'page-history-before-undo');
  });

  await test.step('the button takes back the newest change, writes the undo and marks the change it took back', async () => {
    await history.getByTestId('page-history-undo').click();
    await expect(rows).toHaveCount(ROWS_AFTER_ONE_UNDO);
    await expect(rows.nth(0)).toContainText('An undo');
    await expect(rows.nth(1)).toHaveAttribute('data-undone', 'true');
    await expect(settings.getByTestId('page-settings-none')).toBeVisible();
    await snap(page, 'page-history-after-undo');
  });

  await test.step('Ctrl+Z takes back the edit that is left, and the server keeps all four entries', async () => {
    await history.getByRole('heading').click();
    await page.keyboard.press('Control+z');
    await expect(rows).toHaveCount(ROWS_AFTER_TWO_UNDOS);
    await expect(history.getByTestId('page-history-undo')).toBeDisabled();
    const stored = await readHistory(page, pageId, stepId);
    expect(stored.map((item) => [item.layer, item.source, item.undone])).toEqual([
      ['hand', 'undo', false],
      ['settings', 'undo', false],
      ['settings', 'user', true],
      ['hand', 'user', true],
    ]);
    const edits = await page.request.get(
      `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/edits/geometry`,
    );
    expect(((await edits.json()) as { total: number }).total).toBe(0);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
