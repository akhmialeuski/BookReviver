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
 * Work on many pages at once: a setting of the open page is carried over to the pages after it, which skips the page that
 * has a value of its own and is taken back by one undo, and a run in the mode that replaces the hand settings warns with
 * the number of pages that lose their edit, takes it away when it is confirmed, and gives it back by one undo.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 240_000;
const DESKEW = 'geometry.deskew';
const DESKEW_TITLE = 'Deskew';
const FIELD = 'max_angle';
const SLANT_OF_THE_PAGE = 3;
const SLANT_OF_THE_OTHER_PAGE = 4;
const ANGLE_OF_THE_EDIT = 1.5;
const FIRST = 0;
const SECOND = 1;
const THIRD = 2;

// Tall enough for the pictures of the key states to show the open step with its settings
test.use({ viewport: { width: 1280, height: 1100 } });

interface HistoryItem {
  layer: string;
  source: string;
  undone: boolean;
}

/** The headers a change made straight through the API carries, as the screen sends them. */
async function changeHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  return {
    [CSRF_HEADER_NAME]: cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '',
  };
}

/** Read the identifiers of the pages of the open book in the order of the book. */
async function pageIds(page: Page): Promise<string[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  return items.sort((a, b) => a.position - b.position).map((item) => item.id);
}

/** Set the value a page uses for the field of the step, as the settings of a page do. */
async function putSetting(
  page: Page,
  pageId: string,
  stepId: string,
  value: number,
): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry/${stepId}/${FIELD}`,
    { headers: await changeHeaders(page), data: { value } },
  );
  expect(response.ok()).toBe(true);
}

/** Save the rotation edit of a step as the editor does. */
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

/** Read the fields a page changes for the steps of the stage, by name, one entry for each step that has any. */
async function readSettings(page: Page, pageId: string): Promise<Record<string, number>[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry`,
  );
  const body = (await response.json()) as { items: { params: Record<string, number> }[] };
  return body.items.map((item) => item.params);
}

/** Count the manual edits a page has in the stage. */
async function countEdits(page: Page, pageId: string): Promise<number> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/edits/geometry`,
  );
  return ((await response.json()) as { total: number }).total;
}

/** Read the history of a step on a page from the server, the newest change first. */
async function readHistory(page: Page, pageId: string, stepId: string): Promise<HistoryItem[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/history/geometry/${stepId}?size=100`,
  );
  return ((await response.json()) as { items: HistoryItem[] }).items;
}

test('a reader carries a setting to the pages after it, and a run that replaces the hand settings warns first and is undone in one action', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  // The settings of the page and its history are in the panel of the open step
  const step = page.getByTestId('step-panel');
  const settings = step.getByTestId('page-settings');
  let ids: string[] = [];
  let stepId = '';

  await test.step('a book of three pages: the first has a setting, the third its own value of it, the second an edit', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book changed in batches');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    ids = await pageIds(page);
    [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    expect(stepId).not.toBe('');
    await putSetting(page, ids[FIRST] ?? '', stepId, SLANT_OF_THE_PAGE);
    await putSetting(page, ids[THIRD] ?? '', stepId, SLANT_OF_THE_OTHER_PAGE);
    await saveRotation(page, ids[SECOND] ?? '', stepId);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await page.getByTestId('bar-step').filter({ hasText: DESKEW_TITLE }).click();
    await expect(step).toBeVisible();
    await expect(settings.getByTestId('page-settings-list')).toContainText(
      `Largest slant: ${SLANT_OF_THE_PAGE}`,
    );
  });

  await test.step('carrying the setting to the following pages skips the page that has its own value', async () => {
    await settings.getByTestId('carry-menu').click();
    await page.getByTestId('carry-following').click();
    await expect(settings.getByTestId('carry-result')).toContainText(
      'Carried over to 1 page, 1 page was skipped for a value of their own.',
    );
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([{ [FIELD]: SLANT_OF_THE_PAGE }]);
    expect(await readSettings(page, ids[THIRD] ?? '')).toEqual([
      { [FIELD]: SLANT_OF_THE_OTHER_PAGE },
    ]);
    await snap(page, 'page-batch-carry-over');
  });

  await test.step('one undo takes the value back from the page it reached, and the page with its own value is as it was', async () => {
    await settings.getByTestId('carry-undo').click();
    await expect(settings.getByTestId('carry-result')).toHaveCount(0);
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([]);
    expect(await readSettings(page, ids[THIRD] ?? '')).toEqual([
      { [FIELD]: SLANT_OF_THE_OTHER_PAGE },
    ]);
    const history = await readHistory(page, ids[SECOND] ?? '', stepId);
    expect(history.map((item) => [item.layer, item.source, item.undone])).toEqual([
      ['settings', 'undo', false],
      ['settings', 'carry-over', true],
      ['hand', 'user', false],
    ]);
  });

  await test.step('a run that replaces the hand settings warns with the number of pages that lose their edit', async () => {
    await page.getByTestId('run-mode').selectOption('replace-hand');
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    const dialog = page.getByTestId('overwrite-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('overwrite-pages')).toContainText(
      '1 page loses the shape set by hand',
    );
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
    await snap(page, 'page-batch-warning');
  });

  await test.step('the confirmed run takes the edit away and the history of the page says a run did it', async () => {
    await page.getByTestId('overwrite-confirm').click();
    await expect.poll(() => countEdits(page, ids[SECOND] ?? '')).toBe(0);
    await waitForIdleJobs(page, openProjectId(page));
    const history = await readHistory(page, ids[SECOND] ?? '', stepId);
    expect(history[0]).toMatchObject({ layer: 'hand', source: 'run', undone: false });
    expect(await readSettings(page, ids[FIRST] ?? '')).toEqual([{ [FIELD]: SLANT_OF_THE_PAGE }]);
  });

  await test.step('one undo gives the edit back', async () => {
    const response = await page.request.post(
      `/api/v1/projects/${openProjectId(page)}/pages/${ids[SECOND]}/history/geometry/${stepId}/undo`,
      { headers: await changeHeaders(page), data: {} },
    );
    expect(response.ok()).toBe(true);
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
