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
 * The history of a step on a page, a collapsible section at the end of the panel of the stage: a setting changed in the
 * panel and an edit saved by the editor are listed with what they changed, the button of the newest row takes it back,
 * Ctrl+Z takes back the one before it, and the history keeps the undos beside the changes they took back. A history of
 * several changes is read three at a time, an undo back to an older row asks first, and a clear asks too, then leaves the
 * section grey with no history and no setting on the page.
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
const CHANGES_AFTER_TWO_UNDOS = 4;
const ROWS_SHOWN = 3;
const SLANTS_OF_THE_PAGE = [1, 2, 3, 4];
const OLDEST_SLANT = 1;
const THIRD_ROW = 2;
const CHANGES_TAKEN_BACK = 3;
const CHANGES_AFTER_THE_UNDO_BACK = 7;

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
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/edits/geometry/${stepId}`,
    {
      headers: await changeHeaders(page),
      multipart: { kind: 'rotation', geometry: JSON.stringify({ degrees: ANGLE_OF_THE_EDIT }) },
    },
  );
  expect(response.ok()).toBe(true);
}

/** The headers a change made straight through the API carries, as the screen sends them. */
async function changeHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  return {
    [CSRF_HEADER_NAME]: cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '',
  };
}

/** Set the value a page uses for the largest slant of the step, as the settings of a page do. */
async function putSlant(page: Page, pageId: string, stepId: string, value: number): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry/${stepId}/max_angle`,
    { headers: await changeHeaders(page), data: { value } },
  );
  expect(response.ok()).toBe(true);
}

/** Read the settings a page has for the steps of the Geometry stage, one entry for each step that has any. */
async function readSettings(page: Page, pageId: string): Promise<Record<string, number>[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry`,
  );
  return ((await response.json()) as { items: { params: Record<string, number> }[] }).items.map(
    (item) => item.params,
  );
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
  // The settings of the page are in the panel of the open step, and the history ends the panel of the stage
  const step = page.getByTestId('step-panel');
  const history = page.getByTestId('stage-panel').getByTestId('page-history');
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
    await expect(history.getByTestId('page-history-count')).toHaveText('1 change');
    await history.getByTestId('page-history-toggle').click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('Set by hand');
    await settings.getByTestId('page-settings-edit').click();
    await settings.locator('form input[type="number"]').first().fill(SLANT_OF_THE_PAGE);
    await expect(rows).toHaveCount(CHANGES_WRITTEN);
    await expect(rows.first()).toContainText('Settings of the page');
    await expect(rows.first()).toContainText(SLANT_OF_THE_PAGE);
    await expect(settings.getByTestId('page-settings-list')).toBeVisible();
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-before-undo');
  });

  await test.step('the button takes back the newest change, writes the undo and marks the change it took back', async () => {
    await rows.first().getByTestId('page-history-undo-here').click();
    await expect(rows).toHaveCount(ROWS_AFTER_ONE_UNDO);
    await expect(rows.nth(0)).toContainText('An undo');
    await expect(rows.nth(1)).toHaveAttribute('data-undone', 'true');
    await expect(settings.getByTestId('page-settings-none')).toBeVisible();
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-after-undo');
  });

  await test.step('Ctrl+Z takes back the edit that is left, and the server keeps all four entries', async () => {
    await step.getByTestId('step-panel-title').click();
    await page.keyboard.press('Control+z');
    await expect(history.getByTestId('page-history-count')).toHaveText(
      `${CHANGES_AFTER_TWO_UNDOS} changes`,
    );
    await expect(rows).toHaveCount(ROWS_SHOWN);
    await expect(history.getByTestId('page-history-undo-here')).toHaveCount(0);
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

test('a reader reads a long history three changes at a time, undoes back to an older change after a question, and clears the history after another', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const history = page.getByTestId('stage-panel').getByTestId('page-history');
  const rows = history.getByTestId('page-history-row');
  const dialog = page.getByTestId('page-history-dialog');
  let pageId = '';
  let stepId = '';

  await test.step('a page has a setting changed four times, and its history is collapsed with the number of changes', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with a long history');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('bar-step').first()).toBeVisible();
    pageId = await firstPageId(page);
    [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    expect(stepId).not.toBe('');
    for (const slant of SLANTS_OF_THE_PAGE) {
      await putSlant(page, pageId, stepId, slant);
    }
    await page.getByTestId('bar-step').filter({ hasText: DESKEW_TITLE }).click();
    await expect(history.getByTestId('page-history-count')).toHaveText(
      `${SLANTS_OF_THE_PAGE.length} changes`,
    );
    await expect(history.getByTestId('page-history-toggle')).toBeEnabled();
    await expect(rows).toHaveCount(0);
    await expect(history.getByTestId('page-history-undo-here')).toHaveCount(0);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-collapsed');
  });

  await test.step('opened, it lists the newest three, and the button under them loads the one that is left', async () => {
    await history.getByTestId('page-history-toggle').click();
    await expect(rows).toHaveCount(ROWS_SHOWN);
    await expect(rows.first()).toContainText(`Largest slant: ${SLANTS_OF_THE_PAGE.at(-1)}`);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-three-rows');
    await history.getByTestId('page-history-more').click();
    await expect(rows).toHaveCount(SLANTS_OF_THE_PAGE.length);
    await expect(history.getByTestId('page-history-more')).toHaveCount(0);
  });

  await test.step('an undo to an older row asks first, and takes back that change and the ones after it', async () => {
    await rows.nth(THIRD_ROW).getByTestId('page-history-undo-here').click();
    await expect(dialog).toContainText(`Undo ${CHANGES_TAKEN_BACK} changes?`);
    await dialog.getByTestId('page-history-confirm').click();
    await expect(dialog).toHaveCount(0);
    await expect(history.getByTestId('page-history-count')).toHaveText(
      `${CHANGES_AFTER_THE_UNDO_BACK} changes`,
    );
    expect(await readSettings(page, pageId)).toEqual([{ max_angle: OLDEST_SLANT }]);
  });

  await test.step('a clear asks first, deletes the history and the settings, and leaves the section grey', async () => {
    await history.getByTestId('page-history-clear').click();
    await expect(dialog).toContainText('cannot be undone');
    await snap(page, 'page-history-clear-confirmation');
    await dialog.getByTestId('page-history-confirm').click();
    await expect(dialog).toHaveCount(0);
    await expect(history).toHaveAttribute('aria-disabled', 'true');
    await expect(history.getByTestId('page-history-toggle')).toBeDisabled();
    await expect(history.getByTestId('page-history-reason')).toContainText('Nothing has changed');
    await expect(rows).toHaveCount(0);
    expect(await readHistory(page, pageId, stepId)).toEqual([]);
    expect(await readSettings(page, pageId)).toEqual([]);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-disabled-after-clear');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
