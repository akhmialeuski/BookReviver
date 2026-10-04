import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writeSheetsFolder,
} from './support/account';

/**
 * The step bar and the workspace of a step in Cleanup, which are the ones Geometry has: the bar stands under the row above
 * the canvas with the four steps of the text recipe, Thickness opens on a link of its own with its settings in the panel,
 * the recipe runs up to it on the open page, and the strip of the open step lists the pages that carry a flag the server
 * put on them, such as the pages set by hand.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 120_000;
const THICKNESS = 'cleanup.thickness';
const THICKNESS_INDEX = 2;
const STEP_TITLES = ['Binarization', 'Despeckle', 'Thickness', 'Fill zones'];
const RECIPE_AMOUNT = '1';
const PAGE_AMOUNT = '2';
const STEP_ADDRESS = /\/stages\/cleanup\/steps\/[0-9a-f-]{36}(\?|$)/;

// Tall enough for the pictures of the key states to show the bar, the canvas and the panel together
test.use({ viewport: { width: 1280, height: 1000 } });

/** Count the runs of a stage that ended well in the open book, which tells that a run the reader started is over. */
async function finishedRuns(page: Page): Promise<number> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=50`);
  const items = ((await listed.json()) as { items: { kind: string; state: string }[] }).items;
  return items.filter((job) => job.kind === 'run-stage' && job.state === 'succeeded').length;
}

/** The flags the server puts on each page of the book at a step of Cleanup, in the order of the book. */
async function flagsAt(page: Page, stepId: string): Promise<string[][]> {
  const listed = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/stages/cleanup/pages?step=${stepId}&size=100`,
  );
  const items = ((await listed.json()) as { items: { step: { flags: string[] } }[] }).items;
  return items.map((item) => item.step.flags);
}

test('the steps of Cleanup have a bar and a workspace each, Thickness is set and run, and the strip lists the pages set by hand', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(PAGES);
  const bar = page.getByTestId('step-bar');
  const barSteps = page.getByTestId('bar-step');
  const strip = page.getByTestId('strip-page');
  const flagFilter = page.getByTestId('strip-step-filter');
  const thickness = page.locator(`[data-testid="recipe-step"][data-processor="${THICKNESS}"]`);
  let bookPath = '';
  let stepId = '';

  await test.step('three sheets are cut to their frames by the Geometry stage, which Cleanup reads', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to clean in steps');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(strip).toHaveCount(PAGES);
    const before = await finishedRuns(page);
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
  });

  await test.step('the bar of Cleanup shows its four steps in order, and nothing of the screen is gone', async () => {
    await page.goto(`${bookPath}/stages/cleanup`);
    await expect(page.getByTestId('stage-title')).toHaveText('Cleanup');
    await expect(strip).toHaveCount(PAGES);
    await expect(bar).toBeVisible();
    await expect(barSteps).toHaveCount(STEP_TITLES.length);
    for (const [index, title] of STEP_TITLES.entries()) {
      await expect(barSteps.nth(index)).toContainText(title);
    }
    // The three steps that clean the pages of text carry the mark of the condition
    await expect(page.getByTestId('bar-step-mark')).toHaveText(['¶', '¶', '¶']);
    for (const id of [
      'toggle-strip',
      'toggle-panel',
      'page-strip',
      'recipe-select',
      'run-menu',
      'preview-toggle',
      'this-page',
    ]) {
      await expect(page.getByTestId(id)).toBeVisible();
    }
    await snap(page, 'cleanup-step-bar');
  });

  await test.step('Thickness opens on a link of its own, with its input on the canvas and its settings in the panel', async () => {
    [stepId = ''] = await stepIdsOf(page, 'cleanup', THICKNESS);
    expect(stepId).not.toBe('');
    await barSteps.nth(THICKNESS_INDEX).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page).toHaveURL(new RegExp(`/steps/${stepId}`));
    await expect(page.getByTestId('step-panel-title')).toHaveText('3 · Thickness');
    await expect(barSteps.nth(THICKNESS_INDEX)).toHaveAttribute('aria-current', 'step');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(
      page.getByTestId('step-panel-settings').getByRole('spinbutton', { name: 'Amount' }),
    ).toHaveValue('0');
  });

  await test.step('the amount is changed in the settings of the step, saved, and the recipe is run on the open page', async () => {
    await page
      .getByTestId('step-panel-settings')
      .getByRole('spinbutton', { name: 'Amount' })
      .fill(RECIPE_AMOUNT);
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    const before = await finishedRuns(page);
    await page.getByTestId('step-auto-page').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
    await waitForIdleJobs(page, openProjectId(page));
    await expect(page.getByTestId('step-panel-state')).toContainText('Found by the step', {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(barSteps.nth(THICKNESS_INDEX)).toHaveAttribute('data-state', 'found');
  });

  await test.step('the strip offers the reasons a page asks for a look, and no page is set by hand yet', async () => {
    await expect(flagFilter).toBeVisible();
    await expect(flagFilter.locator('option')).toHaveText([
      'Any page',
      /^Step unsure · \d+$/,
      /^Differs from the book · \d+$/,
      'Set by hand · 0',
      /^Skipped by the condition · \d+$/,
    ]);
    await flagFilter.selectOption('by-hand');
    await expect(strip).toHaveCount(0);
    await flagFilter.selectOption('');
    await expect(strip).toHaveCount(PAGES);
  });

  await test.step('a page that has an amount of its own for the step is set by hand, and the filter lists it alone', async () => {
    const settings = thickness.getByTestId('page-settings');
    if (!(await settings.isVisible())) {
      await thickness.getByTestId('step-toggle').click();
    }
    await settings.getByTestId('page-settings-edit').click();
    await settings.getByRole('spinbutton', { name: 'Amount' }).fill(PAGE_AMOUNT);
    await expect(settings.getByTestId('page-settings-list')).toBeVisible();
    await expect(flagFilter.locator('option').nth(3)).toHaveText('Set by hand · 1');
    expect((await flagsAt(page, stepId)).filter((flags) => flags.includes('by-hand'))).toHaveLength(
      1,
    );
    await flagFilter.selectOption('by-hand');
    await expect(strip).toHaveCount(1);
    await expect(page.getByTestId('step-count-byHand')).toContainText('1 page');
    await snap(page, 'cleanup-thickness-set-by-hand');
  });

  await test.step('the filter is let go, the step stays open as the page changes, and a second press on it closes it', async () => {
    await flagFilter.selectOption('');
    await expect(strip).toHaveCount(PAGES);
    await strip.nth(1).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('step-panel-title')).toHaveText('3 · Thickness');
    await barSteps.nth(THICKNESS_INDEX).click();
    await expect(page).toHaveURL(/\/stages\/cleanup(\?|$)/);
    await expect(page.getByTestId('step-panel')).toHaveCount(0);
    // Without an open step the strip has no reasons to list
    await expect(flagFilter).toHaveCount(0);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
