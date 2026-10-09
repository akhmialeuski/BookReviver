import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
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
 * The catalogue of steps and the window of the gear in the step bar, and the carrying of a shape over to other pages.
 *
 * The plus button of the bar lists the processors of Geometry and adds the one chosen to the recipe, so a second Deskew stands in the bar. The gear opens the steps of the recipe as a list, where a step is removed. A shape set by hand
 * on the first page goes to the following pages in one action, and one undo takes it back from all of them. Exact values
 * come from the field and never from a drag, so no screen distance depends on the size of the page on the canvas.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 90_000;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;
const CARRIED_PAGES = PAGES - 1;
const ANGLE = '1.5';

test.use({ viewport: { width: 1280, height: 1000 } });

test('a step is added from the catalogue twice, removed in the window of the gear, and a shape is carried over', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const barSteps = page.getByTestId('bar-step');
  const deskews = barSteps.filter({ hasText: 'Deskew' });
  const layer = page.getByTestId('editor-layer');
  const angle = page.getByRole('textbox', { name: 'Angle in degrees' });
  const windowOfSteps = page.getByTestId('steps-window');
  let stepsBefore = 0;

  await test.step('a book of three pages opens on Geometry with its step bar', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of added steps');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(barSteps.first()).toBeVisible();
    stepsBefore = await barSteps.count();
    await expect(deskews).toHaveCount(1);
  });

  await test.step('the plus button lists the steps of Geometry, each with a line saying what it does', async () => {
    await page.getByTestId('step-catalogue').click();
    const list = page.getByTestId('step-catalogue-list');
    await expect(list).toBeVisible();
    await expect(list.getByTestId('catalogue-add')).not.toHaveCount(0);
    await expect(list.locator('[data-processor="geometry.deskew"]')).toContainText(
      'Turns the page so its lines of text run level',
    );
    await snap(page, 'catalogue-open');
  });

  await test.step('choosing Deskew again adds a second Deskew after the first one, and opens it', async () => {
    await page
      .getByTestId('step-catalogue-list')
      .locator('[data-processor="geometry.deskew"]')
      .click();
    await expect(barSteps).toHaveCount(stepsBefore + 1);
    await expect(deskews).toHaveCount(2);
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(deskews.nth(1)).toHaveAttribute('data-open', 'true');
    await expect(page.getByTestId('step-catalogue-list')).toHaveCount(0);
    expect(await stepIdsOf(page, 'geometry', 'geometry.deskew')).toHaveLength(2);
  });

  await test.step('the gear opens the steps of the recipe as a list, with the way to keep them as a profile', async () => {
    await page.getByTestId('steps-gear').click();
    await expect(windowOfSteps).toBeVisible();
    await expect(windowOfSteps.getByTestId('recipe-step')).toHaveCount(stepsBefore + 1);
    await expect(windowOfSteps.getByRole('button', { name: 'Save as profile' })).toBeVisible();
    await snap(page, 'steps-window');
  });

  await test.step('removing the second Deskew in the window and saving leaves the first one', async () => {
    await windowOfSteps
      .getByTestId('recipe-step')
      .filter({ hasText: 'Deskew' })
      .nth(1)
      .getByTestId('step-remove')
      .click();
    await expect(windowOfSteps.getByTestId('recipe-step')).toHaveCount(stepsBefore);
    await windowOfSteps.getByTestId('recipe-save').click();
    await expect(windowOfSteps.getByTestId('recipe-save-bar')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(windowOfSteps).toHaveCount(0);
    await expect(barSteps).toHaveCount(stepsBefore);
    await expect(deskews).toHaveCount(1);
    expect(await stepIdsOf(page, 'geometry', 'geometry.deskew')).toHaveLength(1);
  });

  await test.step('"Reset to the default steps" asks first, then puts the steps of the stage back and opens the default step in place of the step that is gone', async () => {
    await page.getByTestId('step-catalogue').click();
    await page
      .getByTestId('step-catalogue-list')
      .locator('[data-processor="geometry.deskew"]')
      .click();
    await expect(barSteps).toHaveCount(stepsBefore + 1);
    await expect(page).toHaveURL(STEP_ADDRESS);

    await page.getByTestId('steps-gear').click();
    await windowOfSteps.getByTestId('steps-reset').click();
    const confirmation = page.getByTestId('steps-reset-dialog');
    await expect(confirmation).toContainText('out of date');
    await snap(page, 'reset-to-the-default-steps');
    await confirmation.getByTestId('steps-reset-confirm').click();

    await expect(barSteps).toHaveCount(stepsBefore);
    await expect(deskews).toHaveCount(1);
    await expect(page).toHaveURL(STEP_ADDRESS);
    // The close button, since Escape pressed while the confirmation hands the focus back can land on neither
    await windowOfSteps.getByRole('button', { name: 'Close' }).click();
    await expect(windowOfSteps).toHaveCount(0);
  });

  await test.step('a shape set by hand on the first page is carried to the following pages in one action', async () => {
    await deskews.first().click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await angle.fill(ANGLE);
    await angle.press('Enter');
    await expect(layer).toHaveAttribute('data-figure', 'by-hand', { timeout: RUN_TIMEOUT_MS });
    await waitForIdleJobs(page, openProjectId(page));

    await page.getByTestId('carry-menu').click();
    await page.getByTestId('carry-following').click();
    await expect(page.getByTestId('carry-result')).toContainText(
      `Carried over to ${CARRIED_PAGES} pages`,
    );
    await snap(page, 'shape-carried-over');

    await page.getByTestId('page-strip').getByTestId('strip-page').nth(1).click();
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    await expect(angle).toHaveValue(ANGLE);
  });

  await test.step('one undo takes the shape back from every page it reached', async () => {
    await page.getByTestId('page-strip').getByTestId('strip-page').first().click();
    await page.getByTestId('carry-undo').click();
    await expect(page.getByTestId('carry-result')).toHaveCount(0);
    await page.getByTestId('page-strip').getByTestId('strip-page').nth(1).click();
    await expect(layer).not.toHaveAttribute('data-figure', 'by-hand');
    await page.getByTestId('page-strip').getByTestId('strip-page').nth(2).click();
    await expect(layer).not.toHaveAttribute('data-figure', 'by-hand');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
