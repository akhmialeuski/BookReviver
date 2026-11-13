import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { MESSAGES } from '../src/shared/messages';
import {
  createBook,
  openBookId,
  openLibrary,
  snap,
  stepIdsOf,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import { test } from './support/fixtures';
import { changeHeaders } from './support/page-work';
import { openStepsWindow, processorsInWindow, stepsWindow, windowStepOf } from './support/steps';

/**
 * The type of a step, which the menu on the card of the step replaces in the window of the gear: the menu lists the
 * processors of the stage the step may become where it stands, its own type marked, and leaves out one that cannot work
 * there. Choosing one keeps the step at its place under its identifier, and the setting the page kept for the earlier
 * type is put aside, and comes back when the earlier type is chosen again. The history of the page keeps both
 * replacements.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const PERSPECTIVE = 'geometry.perspective';
const DESKEW = 'geometry.deskew';
const DEWARP = 'geometry.dewarp';
const CROP = 'geometry.crop';
const NORMALIZE = 'geometry.normalize';
const BUILT_IN = [PERSPECTIVE, DESKEW, DEWARP, CROP, NORMALIZE];
const REPLACED = [PERSPECTIVE, DEWARP, DEWARP, CROP, NORMALIZE];
const SLANT = 3;
const labels = MESSAGES.processing.steps.type;

// Tall enough for the picture of the menu to show the window of the steps
test.use({ viewport: { width: 1280, height: 1000 } });

/** Read the identifier of the page of the open book at a place. */
async function pageIdAt(page: Page, position: number): Promise<string> {
  const listed = await page.request.get(`/api/v1/books/${openBookId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  const found = items.find((item) => item.position === position);
  if (found === undefined) {
    throw new Error(`The book has no page at ${position}.`);
  }
  return found.id;
}

/** Read the settings a page keeps for the steps of the Geometry stage, one entry for each step that has any. */
async function settingsOf(page: Page, pageId: string): Promise<Record<string, unknown>[]> {
  const response = await page.request.get(
    `/api/v1/books/${openBookId(page)}/pages/${pageId}/settings/geometry`,
  );
  return ((await response.json()) as { items: { params: Record<string, unknown> }[] }).items.map(
    (item) => item.params,
  );
}

/** Read the layers of the history of a step on a page from the server, the newest change first. */
async function layersOf(page: Page, pageId: string, stepId: string): Promise<string[]> {
  const response = await page.request.get(
    `/api/v1/books/${openBookId(page)}/pages/${pageId}/history/geometry/${stepId}?size=100`,
  );
  return ((await response.json()) as { items: { layer: string }[] }).items.map(
    (item) => item.layer,
  );
}

/** Open the menu of the type on the card of the step at a place of the window of the gear. */
async function openTypeMenu(page: Page, place: number): Promise<void> {
  const card = stepsWindow(page).getByTestId('recipe-step').nth(place);
  await card.getByTestId('step-type').click();
  await expect(page.getByTestId('step-type-list')).toBeVisible();
}

/** The choice of a processor in the open menu of the type. */
function option(page: Page, processor: string) {
  return page.locator(`[data-testid="step-type-option"][data-processor="${processor}"]`);
}

test('replace the type of a step from the menu of its card and bring the earlier one back', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  let pageId = '';
  let stepId = '';

  await test.step('the first page keeps a setting of the Deskew step of a new book', async () => {
    await openLibrary(page);
    await createBook(page, 'A book for the types of steps');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    pageId = await pageIdAt(page, 0);
    [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    const response = await page.request.put(
      `/api/v1/books/${openBookId(page)}/stages/geometry/steps/${stepId}/values/max_angle`,
      {
        headers: await changeHeaders(page),
        data: { scope: 'pages', page_ids: [pageId], value: SLANT },
      },
    );
    expect(response.ok()).toBe(true);
    await expect
      .poll(() => settingsOf(page, pageId))
      .toEqual([expect.objectContaining({ max_angle: SLANT })]);
  });

  await test.step('the menu of the Deskew card lists the types it may take there, its own marked', async () => {
    await openStepsWindow(page);
    expect(await processorsInWindow(page)).toEqual(BUILT_IN);
    await openTypeMenu(page, 1);
    await expect(page.getByTestId('step-type-list')).toContainText(labels.title);
    await expect(option(page, DESKEW)).toHaveAttribute('data-current', 'true');
    await expect(option(page, DEWARP)).toHaveAttribute('data-current', 'false');
    // Margins must stand after Select content, which comes later, so the step cannot become one where it stands
    await expect(option(page, NORMALIZE)).toHaveCount(0);
    await snap(page, 'step-type-menu');
  });

  await test.step('choosing Dewarp keeps the step at its place and puts the setting of Deskew aside', async () => {
    await option(page, DEWARP).click();
    await expect(page.getByTestId('step-type-list')).toHaveCount(0);
    await expect(windowStepOf(page, DESKEW)).toHaveCount(0);
    expect(await processorsInWindow(page)).toEqual(REPLACED);
    expect(await stepIdsOf(page, 'geometry', DEWARP)).toContain(stepId);
    await expect.poll(() => settingsOf(page, pageId)).toEqual([]);
    await expect.poll(() => layersOf(page, pageId, stepId)).toEqual(['type']);
  });

  await test.step('choosing Deskew again brings the step and the setting of the page back', async () => {
    await openTypeMenu(page, 1);
    await expect(option(page, DEWARP)).toHaveAttribute('data-current', 'true');
    await option(page, DESKEW).click();
    await expect(windowStepOf(page, DESKEW)).toHaveCount(1);
    expect(await processorsInWindow(page)).toEqual(BUILT_IN);
    expect(await stepIdsOf(page, 'geometry', DESKEW)).toEqual([stepId]);
    await expect
      .poll(() => settingsOf(page, pageId))
      .toEqual([expect.objectContaining({ max_angle: SLANT })]);
    await expect.poll(() => layersOf(page, pageId, stepId)).toEqual(['type', 'type', 'settings']);
    await snap(page, 'step-type-back');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
