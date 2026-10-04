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
import {
  countEdits,
  pageIds,
  putSetting,
  readHistory,
  readSettings,
  saveRotation,
} from './support/page-work';

/**
 * Reset of a step to its defaults: the menu offers the four scopes, a reset of the step on every page counts the pages
 * that lose work and waits for the answer, the confirmed reset takes the settings and the hand edit of every page in one
 * batch that the history names, one click on the undo gives them back, and a reset of the step on the open page alone
 * asks nothing.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 240_000;
const DESKEW = 'geometry.deskew';
const FIELD = 'max_angle';
const SLANT_OF_THE_PAGE = 3;
const SLANT_OF_THE_OTHER_PAGE = 4;
const FIRST = 0;
const SECOND = 1;
const THIRD = 2;

// Tall enough for the pictures of the key states to show the open step with its settings
test.use({ viewport: { width: 1280, height: 1100 } });

test('a reader resets a step on every page after a warning with the number of pages, and takes the reset back in one action', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const step = page.locator(`[data-testid="recipe-step"][data-processor="${DESKEW}"]`);
  const settings = step.getByTestId('page-settings');
  let ids: string[] = [];
  let stepId = '';

  await test.step('a book of three pages: the first and the second have a setting, the second also an edit', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book reset to its defaults');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    ids = await pageIds(page);
    [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    expect(stepId).not.toBe('');
    await putSetting(page, ids[FIRST] ?? '', stepId, SLANT_OF_THE_PAGE);
    await putSetting(page, ids[SECOND] ?? '', stepId, SLANT_OF_THE_OTHER_PAGE);
    await saveRotation(page, ids[SECOND] ?? '', stepId);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await step.getByTestId('step-toggle').click();
    await expect(settings.getByTestId('page-settings-list')).toContainText(
      `Largest slant: ${SLANT_OF_THE_PAGE}`,
    );
  });

  await test.step('the menu offers the four scopes', async () => {
    await settings.getByTestId('reset-menu').click();
    await expect(page.getByTestId('reset-page-step')).toHaveText('This step on this page');
    await expect(page.getByTestId('reset-page')).toHaveText('Every step on this page');
    await expect(page.getByTestId('reset-step')).toHaveText('This step on every page');
    await expect(page.getByTestId('reset-stage')).toHaveText(
      'Every step of the stage on every page',
    );
    await snap(page, 'step-reset-menu');
  });

  await test.step('a reset of the step on every page warns with the number of pages and takes nothing before the answer', async () => {
    await page.getByTestId('reset-step').click();
    const dialog = page.getByTestId('reset-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('reset-pages')).toContainText(
      '2 pages lose work of their own: 1 page has a hand edit and 2 pages change a setting.',
    );
    expect(await readSettings(page, ids[FIRST] ?? '')).toEqual([{ [FIELD]: SLANT_OF_THE_PAGE }]);
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
    await snap(page, 'step-reset-warning');
  });

  await test.step('declining the warning changes nothing', async () => {
    await page.getByTestId('reset-dialog').getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByTestId('reset-dialog')).toHaveCount(0);
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([
      { [FIELD]: SLANT_OF_THE_OTHER_PAGE },
    ]);
  });

  await test.step('the confirmed reset takes the settings and the edit of every page, and the history says a reset did it', async () => {
    await settings.getByTestId('reset-menu').click();
    await page.getByTestId('reset-step').click();
    await page.getByTestId('reset-confirm').click();
    await expect(settings.getByTestId('reset-result')).toContainText('Reset 3 layers on 2 pages.');
    expect(await readSettings(page, ids[FIRST] ?? '')).toEqual([]);
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([]);
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(0);
    const history = await readHistory(page, ids[SECOND] ?? '', stepId);
    expect(history.slice(0, 2).map((item) => [item.layer, item.source, item.undone])).toEqual([
      ['hand', 'reset', false],
      ['settings', 'reset', false],
    ]);
    await snap(page, 'step-reset-done');
  });

  await test.step('one undo gives the settings and the edit back on every page', async () => {
    await settings.getByTestId('reset-undo').click();
    await expect(settings.getByTestId('reset-result')).toHaveCount(0);
    expect(await readSettings(page, ids[FIRST] ?? '')).toEqual([{ [FIELD]: SLANT_OF_THE_PAGE }]);
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([
      { [FIELD]: SLANT_OF_THE_OTHER_PAGE },
    ]);
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
  });

  await test.step('a reset of the step on the open page asks nothing and leaves the other pages as they are', async () => {
    await settings.getByTestId('reset-menu').click();
    await page.getByTestId('reset-page-step').click();
    await expect(settings.getByTestId('reset-result')).toContainText('Reset 1 layer on 1 page.');
    await expect(page.getByTestId('reset-dialog')).toHaveCount(0);
    expect(await readSettings(page, ids[FIRST] ?? '')).toEqual([]);
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([
      { [FIELD]: SLANT_OF_THE_OTHER_PAGE },
    ]);
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
    expect(await readSettings(page, ids[THIRD] ?? '')).toEqual([]);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
