import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writeScaledSheetsFolder,
} from './support/account';
import { numbersOf } from './support/layer';

/**
 * Making the pages of a book alike: the book is measured, the Geometry stage puts the block of text of every page on a page of
 * one size with the lines at one distance, and the reader moves the block of one page by hand.
 *
 * The two scans show the same kind of sheet at two scales of the text, as a scan and a photograph of one book do. Measuring the
 * book writes the median line height and the size of a page into the settings of the normalize step, a run of the stage on all
 * pages then gives every page that size, and the move of the block of one page is saved as an edit of that page, which
 * survives a run on all pages until "Auto" takes it away.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const SCALES = [1, 1.2] as const;
const PAGES = SCALES.length;
// What the settings of the step hold before the book is measured: 0 makes each page its block and its margins
const DEFAULT_PAGE_WIDTH = '0';
const NUDGE_KEYS = 2;
const LINE_TOLERANCE = 0.02;
// How many pixels the top margin is made larger than the measured one, to tell it from a measured value
const HAND_MARGIN_GROWTH = 25;
const PLACEMENT = 'Block on the page';
const NORMALIZE = '[data-testid="recipe-step"][data-processor="geometry.normalize"]';

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

test('the book is measured, its pages come out of one size, and the block of a page is moved by hand', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScaledSheetsFolder(SCALES);
  const layer = page.getByTestId('editor-layer');
  const steps = page.getByTestId('editor-step');
  const stepOf = (title: string) => steps.filter({ hasText: title });
  const normalize = page.locator(NORMALIZE);
  const field = (name: string) => normalize.locator(`#root_${name}`);
  // An edit starts a run of the stage on the page, and the next change waits until that run is over
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0);
    await waitForIdleJobs(page, openProjectId(page));
  };
  let pageWidth = 0;
  let pageHeight = 0;
  let lineHeight = 0;
  let projectId = '';
  let block: number[] = [];

  await test.step('the stage runs on the scans with the settings a book starts with', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of two scales');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await runAll(page);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
  });

  await test.step('measuring the book fills the settings of the normalize step from the pages', async () => {
    await normalize.getByTestId('step-toggle').click();
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

  await test.step('the block of the first page is shown on the page of the book, with its frame', async () => {
    await page.getByRole('button', { name: 'Set by hand' }).click();
    await expect(layer).toBeVisible();
    await stepOf(PLACEMENT).click();
    await expect(layer).toHaveAttribute('aria-label', 'Block of text on the page');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    block = await numbersOf(layer, 'data-rect');
    const [, , width = 0, height = 0] = block;
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);
    // The frame lies on a page of the size the step made, and not on the block the crop cut
    expect(width).toBeLessThan(pageWidth);
    expect(height).toBeLessThan(pageHeight);
    await expect(page.getByTestId('rect-hint')).toBeVisible();
    await snap(page, 'normalize-block-frame');
  });

  await test.step('moving the block with the keys is saved for the page and places it again', async () => {
    await layer.focus();
    for (let key = 0; key < NUDGE_KEYS; key += 1) {
      await page.keyboard.press('Shift+ArrowRight');
      await page.keyboard.press('Shift+ArrowDown');
    }
    await expect(stepOf(PLACEMENT)).toHaveAttribute('data-manual', 'true', {
      timeout: RUN_TIMEOUT_MS,
    });
    await settled();
    const moved = await numbersOf(layer, 'data-rect');
    expect(moved[0]).toBeGreaterThan(block[0] ?? 0);
    expect(moved[1]).toBeGreaterThan(block[1] ?? 0);
    expect([moved[2], moved[3]]).toEqual([block[2], block[3]]);
    await expect.poll(async () => (await resultsOf(page, projectId))[0]?.frame).toEqual(moved);
    await snap(page, 'normalize-block-moved');
  });

  await test.step('the move outlives a run on all pages, and the other page is placed by the settings', async () => {
    const moved = await numbersOf(layer, 'data-rect');
    await runAll(page);
    await expect(stepOf(PLACEMENT)).toHaveAttribute('data-manual', 'true');
    await expect(layer).toHaveAttribute('data-rect', moved.join(','));
    const results = await resultsOf(page, projectId);
    expect(results[0]?.frame).toEqual(moved);
    expect(results[1]?.frame).not.toEqual(moved);
    for (const result of results) {
      expect([result.width, result.height]).toEqual([pageWidth, pageHeight]);
    }
  });

  await test.step('Auto takes the move away and the block goes back to where the settings put it', async () => {
    await page.getByTestId('editor-auto').click();
    await expect(stepOf(PLACEMENT)).toHaveAttribute('data-manual', 'false', {
      timeout: RUN_TIMEOUT_MS,
    });
    await settled();
    await expect(layer).toHaveAttribute('data-rect', block.join(','));
    await expect(page.getByTestId('editor-auto')).toBeDisabled();
    await snap(page, 'normalize-after-auto');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
