import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  setContentType,
  snap,
  stepIdsOf,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import {
  changedPages,
  countEdits,
  pageIds,
  putSetting,
  putSideValue,
  type RunPages,
  readStageRows,
  runPages,
  saveRotation,
  selectPagesInGrid,
} from './support/page-work';

/**
 * A stage run step by step: the Geometry recipe is run through its first step on every page from the menu of the run with
 * that step open and checked, then through its last step, and the first step is found in the cache of versions and not
 * computed again.
 *
 * A second book shows what the footer of the panel runs. A run that leaves out the pages with work of their own passes
 * the page with a hand edit by, and a run on this page, on the pages from it on, on the selected pages, on the pages that
 * are out of date, on all pages and on the pages of one kind changes exactly the pages it names, which the rows of the
 * stage tell by the state and the version of each page.
 */

const PAGES = 4;
const SCENARIO_TIMEOUT_MS = 240_000;
const FIRST = 0;
const SECOND = 1;
const THIRD = 2;
const FOURTH = 3;
const EVERY_PAGE = [FIRST, SECOND, THIRD, FOURTH] as const;
const DESKEW = 'geometry.deskew';
// The Deskew step looks for a slant of 5 degrees by default, so each value written for a page differs from what it had
const FIRST_SLANT = 6;

// Tall enough for the pictures of the key states to show the steps of the recipe and the section of the open page
test.use({ viewport: { width: 1280, height: 1000 } });

interface StoredVersion {
  id: string;
  created_at: string;
}

/** Read the full results the Geometry stage made on the first page of the open book, the oldest first. */
async function geometryVersions(page: Page): Promise<StoredVersion[]> {
  const projectId = openProjectId(page);
  const pages = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
  const first = ((await pages.json()) as { items: { id: string; position: number }[] }).items.find(
    (item) => item.position === FIRST,
  );
  if (first === undefined) {
    throw new Error('The book has no first page.');
  }
  const listed = await page.request.get(
    `/api/v1/projects/${projectId}/pages/${first.id}/versions?stage=geometry&scale=full&size=100`,
  );
  const items = ((await listed.json()) as { items: StoredVersion[] }).items;
  return items.toSorted((a, b) => a.created_at.localeCompare(b.created_at));
}

/**
 * Sign in, make a book of the pages of a folder and open it on the Geometry stage.
 *
 * @param page The page of the browser.
 * @param folder The folder of the pages of the book.
 * @param title The title of the book.
 */
async function openGeometryBook(page: Page, folder: string, title: string): Promise<void> {
  await registerAndSignIn(page);
  await createBook(page, title);
  await uploadFolder(page, folder, PAGES);
  const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
  await page.goto(`${bookPath}/stages/geometry`);
  await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
  await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
}

test('the Geometry recipe is run through its first step on all pages, checked, and then through the last step without computing the first again', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const steps = page.getByTestId('bar-step');
  let stepCount = 0;
  let firstStepVersion: StoredVersion | undefined;

  /** Open the step at a place of the bar, unless it is open already. */
  const openStep = async (index: number): Promise<void> => {
    if ((await steps.nth(index).getAttribute('data-open')) !== 'true') {
      await steps.nth(index).click();
    }
    await expect(steps.nth(index)).toHaveAttribute('data-open', 'true');
  };

  /** Run the recipe up to a step on all pages with the step open, and wait for the run to end. */
  const runThrough = async (index: number): Promise<void> => {
    await openStep(index);
    await runPages(page, { throughOpenStep: true });
  };

  await test.step('a book opens on the Geometry stage, whose recipe has several steps', async () => {
    await openGeometryBook(page, folder, 'A book run step by step');
    // The bar draws the steps once the recipe of the stage is read, so they are counted after the first one shows
    await expect(steps.first()).toBeVisible();
    stepCount = await steps.count();
    expect(stepCount).toBeGreaterThan(1);
    // Nothing has run, so no step has been passed and no page stopped short
    await openStep(FIRST);
    await expect(page.getByTestId('strip-stopped-filter')).toHaveCount(0);
  });

  await test.step('a run of all pages through the first step runs it on every page and leaves the rest', async () => {
    await runThrough(FIRST);
    await openStep(stepCount - 1);
    // The stop is told by the strip filter and the section of the page, and the footer has no line for it
    await expect(page.getByTestId('run-stopped')).toHaveCount(0);
    await expect(page.getByTestId('stage-panel').locator('footer')).not.toContainText(
      'Done through',
    );
    const made = await geometryVersions(page);
    expect(made).toHaveLength(1);
    firstStepVersion = made[0];
    await snap(page, 'run-through-the-first-step');
  });

  await test.step('the pages are checked by the step they stopped at', async () => {
    const filter = page.getByTestId('strip-stopped-filter');
    await expect(filter).toBeVisible();
    await filter.selectOption('0');
    await expect(filter.locator('option:checked')).toHaveText(
      new RegExp(`^Stopped at \\S.* · ${PAGES}$`),
    );
    await expect(filter.locator('option:checked')).not.toContainText('step');
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await page.getByTestId('strip-page').first().click();
    await expect(page.getByTestId('this-page-stopped')).toContainText(
      `Run through step 1 of ${stepCount} only`,
    );
    await page.getByTestId('panel-page').scrollIntoViewIfNeeded();
    await snap(page, 'page-stopped-at-the-first-step');
  });

  await test.step('a run of all pages through the last step finds the first in the cache and makes the rest', async () => {
    await runThrough(stepCount - 1);
    await expect(page.getByTestId('run-stopped')).toHaveCount(0);
    await expect(page.getByTestId('strip-stopped-filter')).toHaveCount(0);
    const made = await geometryVersions(page);
    expect(made).toHaveLength(stepCount);
    // The version of the first step is the very one the first run made, so that step was not computed again
    expect(made.find((version) => version.id === firstStepVersion?.id)).toEqual(firstStepVersion);
    await page.getByTestId('strip-page').first().click();
    await expect(page.getByTestId('this-page-stopped')).toHaveCount(0);
    await page.getByTestId('panel-page').scrollIntoViewIfNeeded();
    await snap(page, 'run-through-the-last-step');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

// A scenario of its own, so that each of the two keeps within its time on a machine busy with the other workers
test('the footer runs exactly the pages it names, and leaves the pages with work of their own as they were', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);

  await test.step('a book whose pages have all been run once opens on the Geometry stage', async () => {
    await openGeometryBook(page, folder, 'A book run from the footer');
    await runPages(page);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date');
  });

  let ids: string[] = [];
  let deskewId = '';
  let slant = FIRST_SLANT;

  /** Make the pages at the places out of date by a new value of their own for the slant of the Deskew step. */
  const makeStale = async (positions: readonly number[]): Promise<void> => {
    slant += 1;
    for (const position of positions) {
      await putSetting(page, ids[position] ?? '', deskewId, slant);
    }
    const rows = await readStageRows(page);
    expect(positions.map((position) => rows[position]?.status)).toEqual(
      positions.map(() => 'stale'),
    );
  };

  /**
   * Run the pages the footer names, keeping the work of the pages as it is, and tell which pages the run changed.
   *
   * @param pages The pages the run goes over.
   * @param button What the button of the run says about them before it is pressed.
   */
  const runAndCompare = async (pages: RunPages, button: string): Promise<number[]> => {
    const before = await readStageRows(page);
    await runPages(page, { pages, ownWork: 'keep', button });
    return changedPages(before, await readStageRows(page));
  };

  await test.step('a run that leaves out the pages with work of their own passes the page with a hand edit by', async () => {
    ids = await pageIds(page);
    [deskewId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    expect(deskewId).not.toBe('');
    // The second page has a hand edit, which is work of its own, and the value of a side leaves the others without any
    await saveRotation(page, ids[SECOND] ?? '', deskewId);
    await putSideValue(page, deskewId, 'odd', slant);
    await putSideValue(page, deskewId, 'even', slant);
    expect((await readStageRows(page)).map((row) => row.status)).toEqual(
      EVERY_PAGE.map(() => 'stale'),
    );
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-pages-all').click();
    await page.getByTestId('run-own-skip-own-work').click();
    // The menu counts the pages that have work of their own, and the run leaves them out
    await expect(page.getByTestId('run-own-skip-own-work')).toContainText('\u22121');
    await snap(page, 'run-footer-menu');
    await page.keyboard.press('Escape');

    const before = await readStageRows(page);
    await runPages(page, { ownWork: 'skip-own-work', button: 'all 3 pages' });
    const after = await readStageRows(page);
    expect(changedPages(before, after)).toEqual([FIRST, THIRD, FOURTH]);
    expect(after[SECOND]?.status).toBe('stale');
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
    await expect(page.getByTestId('run-summary')).toContainText('1 page out of date');
  });

  await test.step('a run on this page changes that page and no other', async () => {
    await makeStale(EVERY_PAGE);
    await page.getByTestId('strip-page').nth(SECOND).click();
    await expect(page.getByTestId('strip-page').nth(SECOND)).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(await runAndCompare('page', 'this page')).toEqual([SECOND]);
    await expect(page.getByTestId('run-summary')).toContainText('3 pages out of date');
  });

  await test.step('a run on this page and the pages after it leaves the pages before it as they were', async () => {
    await makeStale(EVERY_PAGE);
    await page.getByTestId('strip-page').nth(THIRD).click();
    await expect(page.getByTestId('strip-page').nth(THIRD)).toHaveAttribute('aria-pressed', 'true');
    expect(await runAndCompare('from-page', '2 pages from this page on')).toEqual([THIRD, FOURTH]);
  });

  await test.step('a run on the selected pages changes the pages that are selected', async () => {
    await makeStale(EVERY_PAGE);
    await selectPagesInGrid(page, [FIRST, THIRD]);
    expect(await runAndCompare('selected', '2 selected pages')).toEqual([FIRST, THIRD]);
  });

  await test.step('a run on the pages that are out of date takes the two that were left', async () => {
    await expect(page.getByTestId('run-summary')).toContainText('2 pages out of date');
    expect(await runAndCompare('attention', '2 out-of-date pages')).toEqual([SECOND, FOURTH]);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date');
  });

  await test.step('a run on all pages changes every page', async () => {
    await makeStale(EVERY_PAGE);
    expect(await runAndCompare('all', 'all 4 pages')).toEqual([...EVERY_PAGE]);
  });

  await test.step('a run on the pages of a kind leaves the pages of the other kind as they were', async () => {
    await setContentType(page, FOURTH, 'color-picture');
    await expect(page.getByTestId('strip-content').nth(FOURTH)).toHaveAttribute(
      'data-content',
      'color-picture',
    );
    await makeStale([FIRST, SECOND, THIRD]);
    await page.getByTestId('run-menu').click();
    await expect(page.getByTestId('run-pages-group:text')).toContainText('3');
    await expect(page.getByTestId('run-pages-group:color-picture')).toContainText('1');
    await page.keyboard.press('Escape');
    expect(await runAndCompare('group:text', '3 pages of the kind Text')).toEqual([
      FIRST,
      SECOND,
      THIRD,
    ]);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
