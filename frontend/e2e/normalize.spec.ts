import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writeScaledSheetsFolder,
} from './support/account';

/**
 * Making the pages of a book alike: the book is measured and the Geometry stage puts the content box of every page on a page of
 * one size with the lines at one distance.
 *
 * The two scans show the same kind of sheet at two scales of the text, as a scan and a photograph of one book do. Measuring the
 * book writes the median line height and the size of the largest box into the settings of the Margins step, and a run of the
 * stage on all pages then gives every page that size. The editor of the box and the margins has a scenario of its own, in
 * `margins-content-box.spec.ts`.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const SCALES = [1, 1.2] as const;
const PAGES = SCALES.length;
// What the settings of the step hold before the book is measured: 0 makes each page its block and its margins
const DEFAULT_PAGE_WIDTH = '0';
const LINE_TOLERANCE = 0.02;
// How many pixels the top margin is made larger than the measured one, to tell it from a measured value
const HAND_MARGIN_GROWTH = 25;
const NORMALIZE_TITLE = 'Margins';

/** What the server holds about the current result of the Geometry stage of one page. */
interface Result {
  width: number;
  height: number;
  lineHeight: number;
  frame: number[];
}

/** Run the stage on all pages and wait until every page is up to date. */
async function runAll(page: Page): Promise<void> {
  await page.getByTestId('run-menu').click();
  await page.getByTestId('run-all').click();
  await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
    timeout: RUN_TIMEOUT_MS,
  });
}

/** Read the data of the current result of the Geometry stage of every page of the book, in the order of the book. */
async function resultsOf(page: Page, projectId: string): Promise<Result[]> {
  const pages = (await (
    await page.request.get(`/api/v1/projects/${projectId}/pages?size=50`)
  ).json()) as { items: { id: string }[] };
  const results: Result[] = [];
  for (const { id } of pages.items) {
    const stages = (await (
      await page.request.get(`/api/v1/projects/${projectId}/pages/${id}/stages`)
    ).json()) as { items: { stage: string; head_version_id: string }[] };
    const head = stages.items.find((item) => item.stage === 'geometry')?.head_version_id;
    const version = (await (
      await page.request.get(`/api/v1/projects/${projectId}/pages/${id}/versions/${head}`)
    ).json()) as { data: Record<string, number | Record<string, number>> };
    const { data } = version;
    const frame = data.frame as Record<string, number>;
    results.push({
      width: data.width_px as number,
      height: data.height_px as number,
      lineHeight: data.line_height_px as number,
      frame: [frame.left, frame.top, frame.width, frame.height].map((value) =>
        Math.round(value ?? 0),
      ),
    });
  }
  return results;
}

test('the book is measured and its pages come out of one size with the lines at one distance', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScaledSheetsFolder(SCALES);
  // The settings of the step are in the panel of the step, which the bar opens
  const normalize = page.getByTestId('step-panel');
  const field = (name: string) => normalize.locator(`#step-panel_${name}`);
  let pageWidth = 0;
  let pageHeight = 0;
  let lineHeight = 0;
  let projectId = '';

  await test.step('the stage runs on the scans with the settings a book starts with', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of two scales');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    await waitForIdleJobs(page, projectId);
    await markPagesAsText(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await runAll(page);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
  });

  await test.step('measuring the book fills the settings of the normalize step from the pages', async () => {
    await page.getByTestId('bar-step').filter({ hasText: NORMALIZE_TITLE }).click();
    await expect(normalize).toBeVisible();
    await expect(field('page_width')).toHaveValue(DEFAULT_PAGE_WIDTH);
    await normalize.getByTestId('measure-book-button').click();
    await expect(field('page_width')).not.toHaveValue(DEFAULT_PAGE_WIDTH, {
      timeout: RUN_TIMEOUT_MS,
    });
    await waitForIdleJobs(page, projectId);
    pageWidth = Number(await field('page_width').inputValue());
    pageHeight = Number(await field('page_height').inputValue());
    lineHeight = Number(await field('line_height').inputValue());
    expect(pageWidth).toBeGreaterThan(0);
    expect(pageHeight).toBeGreaterThan(pageWidth);
    expect(lineHeight).toBeGreaterThan(0);
    // The recipe changed, so the pages made by the old settings are out of date
    await expect(page.getByTestId('run-summary')).toContainText('out of date');
    await snap(page, 'normalize-measured-settings');
  });

  await test.step('a margin set by hand outlives measuring the book, and measured margins can be asked for again', async () => {
    const saveRecipe = async (): Promise<void> => {
      await page.getByTestId('recipe-save').click();
      await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    };
    const measureAgain = async (): Promise<void> => {
      await waitForIdleJobs(page, projectId);
      await normalize.getByTestId('measure-book-button').click();
      await waitForIdleJobs(page, projectId);
    };
    const measuredTop = Number(await field('margin_top').inputValue());
    const handTop = measuredTop + HAND_MARGIN_GROWTH;
    // Typing a margin switches the step to margins set by hand, and the form says so
    await expect(normalize.getByTestId('manual-margins')).toHaveCount(0);
    await field('margin_top').fill(String(handTop));
    await expect(normalize.getByTestId('manual-margins')).toBeVisible();
    await saveRecipe();
    await measureAgain();
    // The page is the median block with the margin that was typed, which is higher by what the margin grew
    await expect(field('page_height')).toHaveValue(String(pageHeight + HAND_MARGIN_GROWTH), {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(field('margin_top')).toHaveValue(String(handTop));
    await expect(field('page_width')).toHaveValue(String(pageWidth));
    await expect(field('line_height')).toHaveValue(String(lineHeight));
    await normalize.getByTestId('use-measured-margins').scrollIntoViewIfNeeded();
    await snap(page, 'normalize-manual-margin-kept');
    // Asking for the measured margins again and measuring puts the measured margin back
    await normalize.getByTestId('use-measured-margins').click();
    await expect(normalize.getByTestId('manual-margins')).toHaveCount(0);
    await saveRecipe();
    await measureAgain();
    await expect(field('margin_top')).toHaveValue(String(measuredTop), {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(field('page_height')).toHaveValue(String(pageHeight));
    await field('margin_top').scrollIntoViewIfNeeded();
    await snap(page, 'normalize-measured-margins-back');
  });

  await test.step('a run on all pages gives every page one size and the lines one distance', async () => {
    await runAll(page);
    const results = await resultsOf(page, projectId);
    expect(results).toHaveLength(PAGES);
    for (const result of results) {
      expect([result.width, result.height]).toEqual([pageWidth, pageHeight]);
      expect(Math.abs(result.lineHeight - lineHeight) / lineHeight).toBeLessThan(LINE_TOLERANCE);
    }
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
