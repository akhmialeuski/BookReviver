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
  changeHeaders,
  countEdits,
  pageIds,
  putSetting,
  readEffective,
  readHistory,
  readSettings,
  saveRotation,
} from './support/page-work';

/**
 * Work on many pages at once: a value of a setting for the even pages reaches the even page and not the page that has a
 * value of its own, and is taken back by one undo, and a run in the mode that replaces the hand settings warns with the
 * number of pages that lose their values and edits, takes them away when it is confirmed, and gives them back by one undo.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 240_000;
const DESKEW = 'geometry.deskew';
const DESKEW_TITLE = 'Deskew';
// The field the helpers of the work of the pages set and read
const FIELD = 'max_angle';
const SLANT_OF_THE_PAGE = 3;
const SLANT_OF_THE_OTHER_PAGE = 4;
const SLANT_OF_THE_EVEN_PAGES = 7;
const FIRST = 0;
const SECOND = 1;
const THIRD = 2;

// Tall enough for the pictures of the key states to show the open step with its settings
test.use({ viewport: { width: 1280, height: 1100 } });

test('a reader sets a value for the even pages and takes it back in one action, and a run that replaces the hand settings warns first and is undone in one action', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  // The settings of the page and its history are in the panel of the open step
  const step = page.getByTestId('panel-settings');
  const slant = step.locator('[data-testid="field-values"][data-field="max_angle"]');
  let ids: string[] = [];
  let stepId = '';

  await test.step('a book of three pages: the first has a setting, the third its own value of it, the second an edit', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book changed in batches');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    ids = await pageIds(page);
    [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    expect(stepId).not.toBe('');
    await putSetting(page, ids[FIRST] ?? '', stepId, SLANT_OF_THE_PAGE);
    await putSetting(page, ids[THIRD] ?? '', stepId, SLANT_OF_THE_OTHER_PAGE);
    await saveRotation(page, ids[SECOND] ?? '', stepId);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await page.getByTestId('bar-step').filter({ hasText: DESKEW_TITLE }).click();
    await expect(step).toBeVisible();
    await expect(slant.getByTestId('value-chip')).toContainText(`${SLANT_OF_THE_PAGE}`);
  });

  await test.step('a value for the even pages reaches the second page and leaves the pages that are odd', async () => {
    await slant.getByTestId('value-add').click();
    await expect(page.getByTestId('value-choice-even')).toContainText(`${Math.floor(PAGES / 2)}`);
    await page.getByTestId('value-choice-even').click();
    // The value just added opens its field at once
    await expect(
      slant.getByTestId('value-chip-edit').filter({ hasText: 'Even pages' }),
    ).toHaveAttribute('aria-expanded', 'true');
    await slant.locator('input[type="number"]').fill(`${SLANT_OF_THE_EVEN_PAGES}`);
    await expect.poll(() => readEffective(page, ids[SECOND] ?? '')).toBe(SLANT_OF_THE_EVEN_PAGES);
    expect(await readEffective(page, ids[FIRST] ?? '')).toBe(SLANT_OF_THE_PAGE);
    expect(await readEffective(page, ids[THIRD] ?? '')).toBe(SLANT_OF_THE_OTHER_PAGE);
    expect(await readSettings(page, ids[SECOND] ?? '')).toEqual([{}]);
    await snap(page, 'page-batch-even-pages');
  });

  await test.step('the cross takes the value back, and the history of the page names the pages it was for', async () => {
    await slant
      .getByTestId('value-chip')
      .filter({ hasText: 'Even pages' })
      .getByTestId('value-chip-remove')
      .click();
    await expect
      .poll(() => readEffective(page, ids[SECOND] ?? ''))
      .not.toBe(SLANT_OF_THE_EVEN_PAGES);
    const history = await readHistory(page, ids[SECOND] ?? '', stepId);
    // The value added for the even pages started from the one of the recipe, which changed nothing on the second page, so
    // the page has a change for the value typed and one for the value taken back
    expect(history.map((item) => [item.layer, item.source, item.undone])).toEqual([
      ['settings', 'user', false],
      ['settings', 'user', false],
      ['hand', 'user', false],
    ]);
  });

  await test.step('a run that drops the work of the pages warns with the number of pages that lose it', async () => {
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-pages-all').click();
    await page.getByTestId('run-own-drop-own-work').click();
    await page.keyboard.press('Escape');
    await page.getByTestId('run-start').click();
    const dialog = page.getByTestId('overwrite-dialog');
    await expect(dialog).toBeVisible();
    // The first page and the third have a value of their own, and the second an edit
    await expect(dialog.getByTestId('overwrite-pages')).toContainText(
      `${PAGES} pages lose the settings and the hand edits`,
    );
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
    await snap(page, 'page-batch-warning');
  });

  await test.step('the confirmed run takes the edit and the values away and the history of the pages says a run did it', async () => {
    await page.getByTestId('overwrite-confirm').click();
    await expect.poll(() => countEdits(page, ids[SECOND] ?? '')).toBe(0);
    await waitForIdleJobs(page, openProjectId(page));
    const history = await readHistory(page, ids[SECOND] ?? '', stepId);
    expect(history[0]).toMatchObject({ layer: 'hand', source: 'run', undone: false });
    const first = await readHistory(page, ids[FIRST] ?? '', stepId);
    expect(first[0]).toMatchObject({ layer: 'settings', source: 'run', undone: false });
  });

  await test.step('one undo gives the edit and the values back on every page', async () => {
    const response = await page.request.post(
      `/api/v1/projects/${openProjectId(page)}/pages/${ids[SECOND]}/history/geometry/${stepId}/undo`,
      { headers: await changeHeaders(page), data: {} },
    );
    expect(response.ok()).toBe(true);
    expect(await countEdits(page, ids[SECOND] ?? '')).toBe(1);
    expect(await readSettings(page, ids[FIRST] ?? '')).toEqual([{ [FIELD]: SLANT_OF_THE_PAGE }]);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
