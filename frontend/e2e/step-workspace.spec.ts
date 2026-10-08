import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  setKind,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';
import { countRunJobs, readStageRows, runPages } from './support/page-work';

/**
 * The step bar and the workspace of a step in Geometry: the bar stands above the canvas with the buttons of the strip and
 * the panel at its ends and takes nothing of the screen away, a step opens on a link of its own with its input on the canvas and its section in the panel, the
 * dots of the bar tell the state of each step on the open page, and the step stays open as the page and the step change.
 *
 * The book has two Deskew steps, one after the other, both for every page of the recipe. A page of text that is changed to a
 * Colour picture from the canvas toolbar moves to the recipe of pictures, which the choice of the recipe counts, and goes out
 * of date without a run, which the button of the footer makes.
 */

const PAGES = 3;
const PLATE_POSITION = 1;
const TEXT_POSITION = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const DESKEW = 'geometry.deskew';
const FIRST_DESKEW_INDEX = 1;
// A step added from the catalogue stands where its processor usually does, which is right after the first one
const SECOND_DESKEW_INDEX = 2;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;

// Tall enough for the pictures of the key states to show the bar, the canvas and the section of the step in the panel
test.use({ viewport: { width: 1280, height: 1000 } });

test('the steps of Geometry have a bar and a workspace each, on a link of their own, and nothing of the stage screen is gone', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const bar = page.getByTestId('step-bar');
  const barSteps = page.getByTestId('bar-step');
  const panel = page.getByTestId('step-panel');
  let stepCount = 0;

  await test.step('a book with a plate opens on Geometry, and the bar of steps holds the buttons of the strip and the panel', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of steps');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await setKind(page, PLATE_POSITION, 'plate');
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(barSteps.first()).toBeVisible();
    stepCount = await barSteps.count();

    await expect(bar).toBeVisible();
    await expect(barSteps).toHaveCount(stepCount);
    await expect(barSteps.nth(0)).toContainText('Perspective');
    // A stage with a bar always has a step open: the last one, since no run has brought a page of the recipe further
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(panel).toHaveCount(1);
    await expect(barSteps.nth(stepCount - 1)).toHaveAttribute('data-open', 'true');
    // The bar takes the place of the row above the canvas, so the buttons of the strip and the panel are its ends
    await expect(bar.getByTestId('toggle-strip')).toBeVisible();
    await expect(bar.getByTestId('toggle-panel')).toBeVisible();
    // No row of buttons stands above the bar: it is the top of the workspace
    const screen = await page.getByTestId('stage-screen').boundingBox();
    const top = await bar.boundingBox();
    expect(screen).not.toBeNull();
    expect(top).not.toBeNull();
    expect(Math.abs((top?.y ?? 0) - (screen?.y ?? 0))).toBeLessThanOrEqual(1);
    await snap(page, 'workspace-step-bar-toggles');
  });

  await test.step('everything the screen had before the bar is where it was', async () => {
    for (const id of [
      'toggle-strip',
      'toggle-panel',
      'page-strip',
      'recipe-select',
      'run-menu',
      'this-page',
    ]) {
      await expect(page.getByTestId(id)).toBeVisible();
    }
    // The steps are in the bar only, and the panel of the recipe lists none
    await expect(page.getByTestId('recipe-steps')).toHaveCount(0);
    await expect(page.getByTestId('step-add')).toHaveCount(0);
  });

  await test.step('a second Deskew is added after the first, which saves it', async () => {
    await page.getByTestId('step-catalogue').click();
    await page.getByTestId('step-catalogue-list').locator(`[data-processor="${DESKEW}"]`).click();
    await expect(barSteps).toHaveCount(stepCount + 1);
    await expect(barSteps.nth(SECOND_DESKEW_INDEX)).toContainText('Deskew');
    // A stage with a bar has a step open all the time, so the added one is open once the address names it
    await expect(barSteps.nth(SECOND_DESKEW_INDEX)).toHaveAttribute('data-open', 'true');
    await expect(page).toHaveURL(STEP_ADDRESS);
    // A step chosen in the catalogue is saved with the recipe at once, so nothing is left to save
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    await expect(barSteps).toHaveCount(stepCount + 1);
  });

  await test.step('the second Deskew is opened, and the recipe is run up to it on every page', async () => {
    await waitForIdleJobs(page, openProjectId(page));
    await barSteps.nth(SECOND_DESKEW_INDEX).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('step-panel-title')).toHaveText(
      `${SECOND_DESKEW_INDEX + 1} · Deskew`,
    );
    await expect(barSteps.nth(SECOND_DESKEW_INDEX)).toHaveAttribute('aria-current', 'step');
    await runPages(page, { throughOpenStep: true });
  });

  await test.step('the first Deskew is open on a page of text: found there, with its input on the canvas and its counts in the panel', async () => {
    const [firstId] = await stepIdsOf(page, 'geometry', DESKEW);
    await barSteps.nth(FIRST_DESKEW_INDEX).click();
    await expect(page).toHaveURL(new RegExp(`/steps/${firstId}`));
    await expect(page.getByTestId('step-panel-title')).toHaveText('2 · Deskew');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(barSteps.nth(FIRST_DESKEW_INDEX)).toHaveAttribute('data-state', 'found');
    // The sections of the panel that were there before the step are under the section of the step
    await expect(page.getByTestId('this-page')).toBeVisible();
    await expect(barSteps).toHaveCount(stepCount + 1);
    await snap(page, 'first-deskew-open-on-a-page-of-text');
  });

  await test.step('the plate shows the recipe of its kind, with the step of the same processor open', async () => {
    await page.getByTestId('strip-page').nth(PLATE_POSITION).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page).toHaveURL(/page=/);
    // The second Deskew was added to the recipe of text, so the recipe of the plate has the steps it had at first
    await expect(barSteps).toHaveCount(stepCount);
    await expect(page.getByTestId('step-panel-title')).toHaveText(
      `${FIRST_DESKEW_INDEX + 1} · Deskew`,
    );
    await snap(page, 'step-open-on-the-plate');
  });

  await test.step('the address of the step opens it again after a reload, and a second press on the open step leaves it open', async () => {
    await page.reload();
    await expect(page.getByTestId('step-panel-title')).toHaveText(
      `${FIRST_DESKEW_INDEX + 1} · Deskew`,
    );
    await expect(barSteps.nth(FIRST_DESKEW_INDEX)).toHaveAttribute('aria-current', 'step');

    await barSteps.nth(FIRST_DESKEW_INDEX).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(panel).toHaveCount(1);
    await expect(page.getByTestId('step-panel-title')).toHaveText(
      `${FIRST_DESKEW_INDEX + 1} · Deskew`,
    );
    await expect(page.getByTestId('this-page')).toBeVisible();
    await expect(barSteps).toHaveCount(stepCount);
  });

  await test.step('a page of text is changed to a Colour picture from the toolbar, which counts it in the recipe of pictures and runs nothing', async () => {
    const recipes = page.getByTestId('recipe-select');
    await page.getByTestId('strip-page').nth(TEXT_POSITION).click();
    await expect(page.getByTestId('strip-page').nth(TEXT_POSITION)).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    // The plate is a picture by its kind, and the two other pages are text
    await expect(recipes).toContainText('Text · 2 pages');
    await expect(recipes).toContainText('Colour picture · 1 page');
    const rowsBefore = await readStageRows(page);
    expect(rowsBefore[TEXT_POSITION]?.status).toBe('fresh');
    await waitForIdleJobs(page, openProjectId(page));
    const runsBefore = await countRunJobs(page);

    await page.getByTestId('content-type-menu').click();
    await page.getByTestId('content-type-color-picture').click();

    await expect(recipes).toContainText('Text · 1 page');
    await expect(recipes).toContainText('Colour picture · 2 pages');
    // The page now stands in the recipe of pictures, which has the steps the plate has
    await expect(barSteps).toHaveCount(stepCount);
    await expect(page.getByTestId('strip-page').nth(TEXT_POSITION)).toContainText('Out of date');
    await expect(page.getByTestId('run-summary')).toContainText('1 page out of date');
    const rowsAfter = await readStageRows(page);
    expect(rowsAfter[TEXT_POSITION]?.status).toBe('stale');
    expect(rowsAfter[TEXT_POSITION]?.kind).toBe('color-picture');
    await waitForIdleJobs(page, openProjectId(page));
    expect(await countRunJobs(page)).toBe(runsBefore);
    await snap(page, 'page-changed-to-a-colour-picture');
  });

  await test.step('the button of the footer runs the page that is out of date, by the recipe of pictures', async () => {
    await runPages(page, { pages: 'attention' });
    const rows = await readStageRows(page);
    expect(rows[TEXT_POSITION]?.status).toBe('fresh');
    expect(rows[TEXT_POSITION]?.kind).toBe('color-picture');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
