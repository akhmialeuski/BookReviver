import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writeSheetsFolder,
} from './support/account';
import { runPages } from './support/page-work';

/**
 * The step bar and the workspace of a step in Cleanup, which are the ones Geometry has: the bar stands above the canvas
 * with the four steps of the text recipe, Thickness opens on a link of its own with its settings in the panel,
 * the recipe runs up to it on the open page, and the strip of the open step lists the pages that carry a flag the server
 * put on them, such as the pages set by hand.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 300_000;
const THICKNESS = 'cleanup.thickness';
const THICKNESS_INDEX = 2;
const STEP_TITLES = ['Binarization', 'Despeckle', 'Thickness', 'Fill zones'];
const RECIPE_AMOUNT = '1';
const PAGE_AMOUNT = '2';
const STEP_ADDRESS = /\/stages\/cleanup\/steps\/[0-9a-f-]{36}(\?|$)/;

// Tall enough for the pictures of the key states to show the bar, the canvas and the panel together
test.use({ viewport: { width: 1280, height: 1000 } });

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
  // The values of the page for the amount are the chips under that setting, in the panel of the open step
  const amount = page
    .getByTestId('panel-settings')
    .locator('[data-testid="field-values"][data-field="amount"]');
  let bookPath = '';
  let stepId = '';

  await test.step('three sheets are cut to their frames by the Geometry stage, which Cleanup reads', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to clean in steps');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(strip).toHaveCount(PAGES);
    await runPages(page);
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
    for (const id of [
      'toggle-strip',
      'toggle-panel',
      'page-strip',
      'recipe-select',
      'run-menu',
      'panel-page',
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
    await expect(page.getByTestId('step-panel-title')).toHaveText('Thickness');
    await expect(barSteps.nth(THICKNESS_INDEX)).toHaveAttribute('aria-current', 'step');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(
      page.getByTestId('panel-settings').getByRole('spinbutton', { name: 'Amount' }),
    ).toHaveValue('0');
  });

  await test.step('the amount is changed in the settings of the step, saved, and the recipe is run on the open page', async () => {
    await page
      .getByTestId('panel-settings')
      .getByRole('spinbutton', { name: 'Amount' })
      .fill(RECIPE_AMOUNT);
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    await runPages(page, { pages: 'page', throughOpenStep: true });
    await expect(barSteps.nth(THICKNESS_INDEX)).toHaveAttribute('data-state', 'found');
  });

  await test.step('the strip offers the reasons a page asks for a look, and no page is set by hand yet', async () => {
    await expect(flagFilter).toBeVisible();
    await expect(flagFilter.locator('option')).toHaveText([
      'Any page',
      /^Step unsure · \d+$/,
      /^Differs from the book · \d+$/,
      'Set by hand · 0',
      /^Skipped: a leaf the program drew · \d+$/,
    ]);
    await flagFilter.selectOption('by-hand');
    await expect(strip).toHaveCount(0);
    await flagFilter.selectOption('');
    await expect(strip).toHaveCount(PAGES);
  });

  await test.step('a page that has an amount of its own for the step is set by hand, and the filter lists it alone', async () => {
    await amount.getByTestId('value-add').click();
    await page.getByTestId('value-choice-page').click();
    // The value just added opens its field at once
    await expect(amount.getByTestId('value-chip-edit')).toHaveAttribute('aria-expanded', 'true');
    await amount.getByRole('spinbutton').fill(PAGE_AMOUNT);
    await expect(amount.getByTestId('value-chip')).toBeVisible();
    await expect(flagFilter.locator('option').nth(3)).toHaveText('Set by hand · 1');
    expect((await flagsAt(page, stepId)).filter((flags) => flags.includes('by-hand'))).toHaveLength(
      1,
    );
    await flagFilter.selectOption('by-hand');
    await expect(strip).toHaveCount(1);
    await snap(page, 'cleanup-thickness-set-by-hand');
  });

  await test.step('the filter is let go, the step stays open as the page changes and when it is pressed again', async () => {
    await flagFilter.selectOption('');
    await expect(strip).toHaveCount(PAGES);
    await strip.nth(1).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('step-panel-title')).toHaveText('Thickness');
    await barSteps.nth(THICKNESS_INDEX).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('step-panel-title')).toHaveText('Thickness');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
