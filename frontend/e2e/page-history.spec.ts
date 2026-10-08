import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Locator, type Page, test } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../src/shared/http/csrf';
import {
  createBook,
  markPagesAsText,
  openImportStage,
  openOrderStage,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
  writeSheetsFolder,
} from './support/account';
import { dragFrom, pairOf } from './support/layer';
import { CHANGE_ROWS, openTimeline, RESULT_ROWS, runPages } from './support/page-work';

/**
 * The history of a page, a collapsible section at the end of the panel of every stage that lists the changes of the open
 * step and its results in one timeline: a setting changed in the panel and an edit saved by the editor are listed with
 * what they changed, the button of the newest row takes it back, Ctrl+Z takes back the one before it, and the history
 * keeps the undos beside the changes they took back. A history of several changes is read three at a time, an undo back
 * to an older row asks first, and a clear asks too, then leaves the section grey with no history and no setting on the
 * page. A hand edit shows in the timeline as soon as it is saved, with the result it made, and a result can be marked and
 * commented and narrowed by the filters. A clear of the step deletes its changes and its results too, and the stage of
 * the page stands on what the step read, before and after a reload. A stage that keeps no history shows the same
 * section grey, and the section never makes the panel scroll sideways.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const DESKEW = 'geometry.deskew';
const DESKEW_TITLE = 'Deskew';
const CROP = 'geometry.crop';
const FIRST_PAGE = 0;
const ANGLE_OF_THE_EDIT = 1.5;
const SLANT_OF_THE_PAGE = '3';
// The edit by hand, the value added for the page with the value of the recipe, and the value typed into it
const CHANGES_WRITTEN = 3;
const CHANGES_AFTER_THE_UNDOS = 6;
const ROWS_SHOWN = 3;
const SLANT_OF_THE_RECIPE = '5';
const SLANTS_OF_THE_PAGE = [1, 2, 3, 4];
const OLDEST_SLANT = 1;
const THIRD_ROW = 2;
const CHANGES_TAKEN_BACK = 3;
const CHANGES_AFTER_THE_UNDO_BACK = 7;
const RUN_TIMEOUT_MS = 90_000;
const DRAG_PX = 30;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;
const COMMENT = 'The frame found is right.';
const EVENTS_AFTER_THE_EDIT = 3;
const EVENTS_AFTER_THE_UNDO = 4;

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

/** Set the value a page uses for the largest slant of the step, as a value for the open page does. */
async function putSlant(page: Page, pageId: string, stepId: string, value: number): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/stages/geometry/steps/${stepId}/values/max_angle`,
    { headers: await changeHeaders(page), data: { scope: 'pages', page_ids: [pageId], value } },
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

/** What the server says of a page at a step of the stage: where the stage of the page stands, and what the step read and made. */
interface StepRow {
  page_id: string;
  status: string;
  version: { id: string } | null;
  step: {
    state: string;
    version: { id: string } | null;
    input_version: { id: string } | null;
  };
}

/** Read the row of a page at a step of the Geometry stage from the server. */
async function readStepRow(page: Page, pageId: string, stepId: string): Promise<StepRow> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/stages/geometry/pages?step=${stepId}&size=100`,
  );
  const found = ((await response.json()) as { items: StepRow[] }).items.find(
    (row) => row.page_id === pageId,
  );
  if (found === undefined) {
    throw new Error('The stage has no row for the page.');
  }
  return found;
}

test('a reader sees what changed on a page, takes the last change back with the button and Ctrl+Z, and finds the undos in the history', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  // The settings of the page are in the panel of the open step, and the history ends the panel of the stage
  const step = page.getByTestId('panel-settings');
  const history = page.getByTestId('stage-panel').getByTestId('page-history');
  const rows = history.locator(CHANGE_ROWS);
  // The values of the page for the largest slant are the chips under that setting, in the panel of the open step
  const slant = step.getByTestId('field-values').and(page.locator('[data-field="max_angle"]'));
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
    await expect(history.getByTestId('page-history-count')).toHaveText('1 event');
    await history.getByTestId('page-history-toggle').click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('Set by hand');
    await slant.getByTestId('value-add').click();
    await page.getByTestId('value-choice-page').click();
    // The value just added opens its field at once
    await expect(slant.getByTestId('value-chip-edit')).toHaveAttribute('aria-expanded', 'true');
    await slant.locator('input[type="number"]').fill(SLANT_OF_THE_PAGE);
    await expect(rows).toHaveCount(CHANGES_WRITTEN);
    await expect(rows.first()).toContainText('Settings of the page');
    await expect(rows.first()).toContainText(SLANT_OF_THE_PAGE);
    await expect(slant.getByTestId('value-chip')).toBeVisible();
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-before-undo');
  });

  await test.step('the button takes back the newest change, writes the undo and marks the change it took back', async () => {
    await rows.first().getByTestId('page-history-undo-here').click();
    await expect(rows).toHaveCount(ROWS_SHOWN);
    await expect(rows.nth(0)).toContainText('An undo');
    await expect(rows.nth(1)).toHaveAttribute('data-undone', 'true');
    // The value the page was given when it was added is left
    await expect(slant.getByTestId('value-chip')).toContainText(SLANT_OF_THE_RECIPE);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-after-undo');
  });

  await test.step('Ctrl+Z takes back the value and then the edit that are left, and the server keeps every entry', async () => {
    await page.getByTestId('step-panel-title').click();
    await page.keyboard.press('Control+z');
    await expect(slant.getByTestId('value-chip')).toHaveCount(0);
    await page.keyboard.press('Control+z');
    await expect(history.getByTestId('page-history-count')).toHaveText(
      `${CHANGES_AFTER_THE_UNDOS} events`,
    );
    await expect(rows).toHaveCount(ROWS_SHOWN);
    await expect(history.getByTestId('page-history-undo-here')).toHaveCount(0);
    const stored = await readHistory(page, pageId, stepId);
    expect(stored.map((item) => [item.layer, item.source, item.undone])).toEqual([
      ['hand', 'undo', false],
      ['settings', 'undo', false],
      ['settings', 'undo', false],
      ['settings', 'user', true],
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
  const rows = history.locator(CHANGE_ROWS);
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
      `${SLANTS_OF_THE_PAGE.length} events`,
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
      `${CHANGES_AFTER_THE_UNDO_BACK} events`,
    );
    expect(await readSettings(page, pageId)).toEqual([{ max_angle: OLDEST_SLANT }]);
  });

  await test.step('a clear asks first, deletes the history and the settings, and leaves the section grey', async () => {
    // The undo changed the settings, so the editor of the step asks for a preview of the page, and a clear is refused
    // while that preview may read the versions the clear deletes
    await waitForIdleJobs(page, openProjectId(page));
    await history.getByTestId('page-history-clear').click();
    await expect(dialog).toContainText('cannot be undone');
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-clear-confirmation');
    await dialog.getByTestId('page-history-confirm').click();
    await expect(dialog).toHaveCount(0);
    await expect(history).toHaveAttribute('aria-disabled', 'true');
    await expect(history.getByTestId('page-history-toggle')).toBeDisabled();
    await expect(history.getByTestId('page-history-reason')).toContainText('Nothing has happened');
    await expect(rows).toHaveCount(0);
    expect(await readHistory(page, pageId, stepId)).toEqual([]);
    expect(await readSettings(page, pageId)).toEqual([]);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'page-history-disabled-after-clear');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

/** Tell whether a scrolling element holds its content without a horizontal scroll bar. */
function fitsTheWidth(area: Locator): Promise<boolean> {
  return area.evaluate((element) => element.scrollWidth <= element.clientWidth);
}

test('a reader reads the changes and the results of a step in one timeline that stays on every stage and never scrolls sideways', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(1);
  const history = page.getByTestId('stage-panel').getByTestId('page-history');
  const area = page.getByTestId('stage-panel').getByTestId('stage-panel-scroll');
  const rows = history.getByTestId('page-history-list').locator(':scope > li');
  const changes = history.locator(CHANGE_ROWS);
  const results = history.locator(RESULT_ROWS);
  const dialog = page.getByTestId('page-history-dialog');
  const layer = page.getByTestId('editor-layer');
  let projectId = '';
  let bookPath = '';

  // An edit starts a run of the stage on the page, and the next change waits until that run is over
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0, { timeout: RUN_TIMEOUT_MS });
    await waitForIdleJobs(page, projectId);
  };

  await test.step('a run through Select content makes a result, which the collapsed section counts', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with a timeline');
    await uploadFolder(page, folder, 1);
    projectId = openProjectId(page);
    await waitForIdleJobs(page, projectId);
    // The detection takes the sheet for a picture, which the recipe for pictures processes, and the step open here
    // belongs to the recipe for text, so the page is said to be text
    await markPagesAsText(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(1);
    await page.getByTestId('bar-step').filter({ hasText: 'Select content' }).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(layer).toHaveAttribute('aria-label', 'Frame of the content');
    // The page has no result yet, so the section is grey and says so
    await expect(history).toHaveAttribute('aria-disabled', 'true');
    await expect(history.getByTestId('page-history-reason')).toContainText('Nothing has happened');
    await runPages(page, { throughOpenStep: true });
    await expect(history.getByTestId('page-history-count')).toHaveText('1 event');
    await expect(history).toHaveAttribute('aria-disabled', 'false');
    await expect(history.getByTestId('page-history-toggle')).toHaveAttribute(
      'data-state',
      'closed',
    );
    await expect(area.locator(':scope > *').last()).toHaveAttribute('data-testid', 'page-history');
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'timeline-collapsed');
  });

  await test.step('opened, the result is a row with its chip, and it is marked good and commented', async () => {
    await openTimeline(page);
    await expect(rows).toHaveCount(1);
    await expect(results).toHaveCount(1);
    await expect(changes).toHaveCount(0);
    await expect(results.first()).toContainText('Result');
    await expect(results.first()).toContainText('Current');
    await expect(results.first().getByTestId('page-history-origin')).toHaveText('Made by the step');
    await results.first().getByTestId('result-mark-good').click();
    await expect(results.first().getByTestId('result-mark-good')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await results.first().getByTestId('result-comment-edit').click();
    await results.first().getByRole('textbox', { name: 'Comment' }).fill(COMMENT);
    await results.first().getByTestId('result-comment-save').click();
    await expect(results.first().getByTestId('result-comment')).toHaveText(COMMENT);

    // The mark and the comment are the server's, so a reload finds both
    await page.reload();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await openTimeline(page);
    await expect(results).toHaveCount(1);
    await expect(results.first().getByTestId('result-mark-good')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(results.first().getByTestId('result-comment')).toHaveText(COMMENT);
  });

  await test.step('a border moved by hand shows its change and the result it made with no reload, and the panel does not scroll sideways', async () => {
    const bottom = await pairOf(layer, 'data-handle-bottom');
    await dragFrom(page, layer, bottom, { x: 0, y: -DRAG_PX });
    await expect(layer).toHaveAttribute('data-figure', 'by-hand', { timeout: RUN_TIMEOUT_MS });
    await settled();
    await expect(history.getByTestId('page-history-count')).toHaveText(
      `${EVENTS_AFTER_THE_EDIT} events`,
      { timeout: RUN_TIMEOUT_MS },
    );
    await expect(rows).toHaveCount(EVENTS_AFTER_THE_EDIT);
    await expect(rows.nth(0)).toHaveAttribute('data-kind', 'result');
    await expect(rows.nth(1)).toHaveAttribute('data-kind', 'change');
    await expect(rows.nth(2)).toHaveAttribute('data-kind', 'result');
    await expect(rows.nth(0).getByTestId('page-history-origin')).toHaveText('Set by hand');
    await expect(rows.nth(1)).toContainText('Set by hand');
    await expect(rows.nth(1)).toContainText('Frame: left');
    await expect(rows.nth(1)).not.toContainText('{');
    await expect.poll(() => fitsTheWidth(area)).toBe(true);
    await expect.poll(() => fitsTheWidth(history)).toBe(true);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'timeline-mixed-rows');
  });

  await test.step('the filters narrow the list to the changes, to the results and to the results marked good', async () => {
    await history.getByTestId('page-history-filter-changes').click();
    await expect(changes).toHaveCount(1);
    await expect(results).toHaveCount(0);
    await history.getByTestId('page-history-filter-results').click();
    await expect(results).toHaveCount(2);
    await expect(changes).toHaveCount(0);
    await history.getByTestId('page-history-filter-good').click();
    await expect(results).toHaveCount(1);
    await expect(results.first().getByTestId('result-comment')).toHaveText(COMMENT);
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'timeline-results-filter');
    await history.getByTestId('page-history-filter-all').click();
    await expect(rows).toHaveCount(EVENTS_AFTER_THE_EDIT);
  });

  await test.step('"Undo to here" takes back the change, and the fourth row waits behind "Show 1 more"', async () => {
    await changes.first().getByTestId('page-history-undo-here').click();
    await expect(history.getByTestId('page-history-count')).toHaveText(
      `${EVENTS_AFTER_THE_UNDO} events`,
    );
    await expect(changes).toHaveCount(2);
    await expect(changes.first()).toContainText('An undo');
    await expect(changes.nth(1)).toHaveAttribute('data-undone', 'true');
    await expect(rows).toHaveCount(EVENTS_AFTER_THE_EDIT);
    await expect(history.getByTestId('page-history-more')).toHaveText('Show 1 more');
    await history.getByTestId('page-history-more').click();
    await expect(rows).toHaveCount(EVENTS_AFTER_THE_UNDO);
    await expect.poll(() => fitsTheWidth(area)).toBe(true);
  });

  await test.step('Cleanup opens on a step, and its history lists the changes of that step and the result of the stage', async () => {
    const started = await page.request.post(`/api/v1/projects/${projectId}/stages/cleanup/run`, {
      headers: await changeHeaders(page),
      data: {},
    });
    expect(started.ok()).toBe(true);
    await waitForIdleJobs(page, projectId);
    await page.goto(`${bookPath}/stages/cleanup`);
    await expect(page.getByTestId('stage-title')).toHaveText('Cleanup');
    await expect(page.getByTestId('strip-page')).toHaveCount(1);
    await expect(page).toHaveURL(/\/stages\/cleanup\/steps\//);
    await expect(page.getByTestId('panel-step')).toHaveCount(1);
    await expect(history).toHaveAttribute('aria-disabled', 'false');
    await expect(history.getByTestId('page-history-count')).toHaveText('1 event', {
      timeout: RUN_TIMEOUT_MS,
    });
    await openTimeline(page);
    await expect(results).toHaveCount(1);
    await expect(results.first()).toHaveAttribute('data-current', 'true');
    // With a step open the section is the history of that step, which has its Changes filter and its clear
    await expect(history.getByTestId('page-history-filter-changes')).toBeEnabled();
    await expect(history.getByTestId('page-history-clear')).toHaveCount(1);
  });

  await test.step('a stage that cannot be worked in yet shows the section grey, with the reason', async () => {
    await page.goto(`${bookPath}/stages/layout`);
    await expect(page.getByTestId('stage-title')).toHaveText('Layout');
    await expect(page.getByTestId('stage-panel')).toContainText('Soon');
    await expect(history).toHaveAttribute('aria-disabled', 'true');
    await expect(history.getByTestId('page-history-reason')).toContainText(
      'keeps no history of its pages',
    );
    await expect(area.locator(':scope > *').last()).toHaveAttribute('data-testid', 'page-history');
  });

  await test.step('Import and Order show the same section grey, with the reason that they keep no history', async () => {
    await openImportStage(page);
    await expect(history).toHaveAttribute('aria-disabled', 'true');
    await expect(history.getByTestId('page-history-toggle')).toBeDisabled();
    await expect(history.getByTestId('page-history-reason')).toContainText(
      'keeps no history of its pages',
    );
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'timeline-import-disabled');

    await openOrderStage(page);
    const order = page.getByTestId('page-history').first();
    await expect(order).toHaveAttribute('aria-disabled', 'true');
    await expect(order.getByTestId('page-history-reason')).toContainText(
      'keeps no history of its pages',
    );
  });

  await test.step('clearing the history asks first, deletes the changes and the results, and takes the step back to where it was before it ran on the page', async () => {
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(1);
    await page.getByTestId('bar-step').filter({ hasText: 'Select content' }).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    const pageId = await firstPageId(page);
    const [stepId = ''] = await stepIdsOf(page, 'geometry', CROP);
    const before = await readStepRow(page, pageId, stepId);
    expect(before.step.version).not.toBeNull();
    expect(before.step.input_version).not.toBeNull();
    await history.getByTestId('page-history-clear').click();
    await expect(dialog).toContainText('the results of the later steps of the stage on this page');
    await dialog.getByTestId('page-history-confirm').click();
    await expect(dialog).toHaveCount(0);

    // The section is grey, the step is at its defaults with no frame, and the stage stands on what the step read
    const cleared = async (): Promise<void> => {
      await expect(history).toHaveAttribute('aria-disabled', 'true');
      await expect(history.getByTestId('page-history-reason')).toContainText(
        'Nothing has happened on this page yet.',
      );
      await expect(history.getByTestId('page-history-count')).toHaveCount(0);
      await expect(layer).toHaveAttribute('data-figure', 'default');
      expect(await readSettings(page, pageId)).toEqual([]);
      expect(await readHistory(page, pageId, stepId)).toEqual([]);
      const row = await readStepRow(page, pageId, stepId);
      expect(row.step.version).toBeNull();
      expect(row.step.state).toBe('default');
      expect(row.status).toBe('stale');
      expect(row.version?.id).toBe(before.step.input_version?.id);
    };
    await cleared();
    await history.scrollIntoViewIfNeeded();
    await snap(page, 'timeline-cleared');
    await page.reload();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await cleared();
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
