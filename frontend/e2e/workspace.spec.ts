import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  readPlace,
  registerAndSignIn,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * The stage workspace of a book: it opens on a stage, the stage bar shows the ten stages with their state, moving
 * between stages changes the address, the page, the layout and the filter live in the address and come back after a
 * reload, the strip turns into a grid for picking pages, the side parts collapse, and the keys and the activity of the
 * book work from any stage.
 */

const PAGES = 6;
const STAGE_NAMES = [
  'Import',
  'Split',
  'Order',
  'Geometry',
  'Cleanup',
  'Layout',
  'Background',
  'Recognition',
  'Proofreading',
  'Typesetting',
];

test('a reader works through the stages of a book', async ({ page }) => {
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const caption = page.getByTestId('canvas-caption');
  const bar = page.getByTestId('stage-bar');
  let bookPath = '';

  await test.step('a new book opens on the Import stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book by stages');
    await expect(page).toHaveURL(/\/projects\/[^/]+\/stages\/import$/);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await expect(page.getByTestId('stage-title')).toHaveText('Import');
    await expect(
      page.getByTestId('stage-bar').getByRole('link', { name: /About the book/ }),
    ).toBeVisible();
  });

  await test.step('upload pages, so every stage has something to show', async () => {
    await uploadFolder(page, folder, PAGES);
    await page.goto(`${bookPath}/stages/page-order`);
    await expect(page.getByTestId('stage-title')).toHaveText('Order');
  });

  await test.step('the stage bar shows the ten stages with their state', async () => {
    for (const name of STAGE_NAMES) {
      await expect(bar.getByRole('link', { name: new RegExp(`^${name}`) })).toBeVisible();
    }
    await expect(bar.getByRole('link', { name: /^Order/ })).toHaveAttribute('aria-current', 'page');
    // A stage without a processor says so, and still opens
    await expect(page.getByTestId('stage-cleanup')).toContainText('Soon');
    await expect(page.getByTestId('stage-page-order')).toContainText('6 pages');
    await expect(page.getByTestId('stage-import')).toContainText('6 files');
  });

  await test.step('moving between stages changes the address', async () => {
    await bar.getByRole('link', { name: /^Geometry/ }).click();
    await expect(page).toHaveURL(/\/stages\/geometry$/);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('stage-summary')).toContainText('Straighten each page');

    await bar.getByRole('link', { name: /^Cleanup/ }).click();
    await expect(page).toHaveURL(/\/stages\/cleanup$/);
    await expect(page.getByTestId('stage-panel')).toContainText('Soon');

    // Alt and a digit go to a stage by its place in the bar
    await page.keyboard.press('Alt+4');
    await expect(page).toHaveURL(/\/stages\/geometry$/);
    await page.keyboard.press('Alt+0');
    await expect(page).toHaveURL(/\/stages\/typesetting$/);
  });

  await test.step('the book opens on the stage it was left on, unless the stage cannot be worked on', async () => {
    const projectId = openProjectId(page);
    // The place is written a second after the last move, and a stage without a processor is not worked on
    await expect
      .poll(async () => (await readPlace(page, projectId)).place?.stage)
      .toBe('typesetting');
    await page.goto(bookPath);
    await expect(page.getByTestId('stage-screen')).toBeVisible();
    await expect(page).not.toHaveURL(/\/stages\/typesetting/);

    await page.getByTestId('stage-geometry').click();
    await expect(page).toHaveURL(/\/stages\/geometry$/);
    await expect.poll(async () => (await readPlace(page, projectId)).place?.stage).toBe('geometry');
    await page.goto(bookPath);
    await expect(page).toHaveURL(/\/stages\/geometry$/);
  });

  await test.step('the strip lists the pages and turns the canvas', async () => {
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(caption).toContainText('1 of 6');

    await page.getByTestId('strip-page').nth(2).click();
    await expect(page).toHaveURL(/page=/);
    await expect(caption).toContainText('3 of 6');
    await expect(page.getByTestId('strip-page').nth(2)).toHaveAttribute('aria-pressed', 'true');

    await page.keyboard.press('ArrowRight');
    await expect(caption).toContainText('4 of 6');
    await page.getByRole('button', { name: 'Previous page' }).click();
    await expect(caption).toContainText('3 of 6');
  });

  await test.step('the layout and the filter are in the address and survive a reload', async () => {
    await page.getByTestId('canvas-spread').click();
    await expect(page).toHaveURL(/view=spread/);
    await page.getByTestId('strip-filter-check').click();
    await expect(page).toHaveURL(/filter=check/);
    // Nothing was processed, so no page needs a look
    await expect(page.getByTestId('strip-page')).toHaveCount(0);

    const address = page.url();
    await page.reload();
    await expect(page).toHaveURL(address);
    await expect(page.getByTestId('strip-filter-check')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('canvas-spread')).toHaveAttribute('aria-pressed', 'true');
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(caption).toContainText('2 of 6');
    await expect(caption).toContainText('3 of 6');

    await page.getByTestId('strip-filter-all').click();
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
  });

  await test.step('the grid picks many pages', async () => {
    await page.getByTestId('strip-view-switch').click();
    await expect(page).toHaveURL(/view=grid/);
    const tiles = page.getByTestId('strip-page');
    await expect(tiles).toHaveCount(PAGES);

    await tiles.nth(1).click();
    await tiles.nth(3).click({ modifiers: ['Shift'] });
    await expect(page.getByTestId('grid-selection')).toHaveText('3 pages selected');
    await tiles.nth(5).click({ modifiers: ['ControlOrMeta'] });
    await expect(page.getByTestId('grid-selection')).toHaveText('4 pages selected');
    await page.getByRole('button', { name: 'Clear the selection' }).click();
    await expect(page.getByTestId('grid-selection')).toHaveText('No pages selected');

    await tiles.nth(4).dblclick();
    await expect(page).not.toHaveURL(/view=grid/);
    await expect(caption).toContainText('5 of 6');
  });

  await test.step('the side parts collapse and keep their state across a reload', async () => {
    // The panels of the workspace carry their ids as test ids
    const strip = page.getByTestId('strip');
    await expect(strip).toBeVisible();
    await page.getByTestId('toggle-strip').click();
    await expect(strip).toBeHidden();

    await page.reload();
    await expect(page.getByTestId('toggle-strip')).toBeVisible();
    await expect(strip).toBeHidden();

    await page.getByTestId('toggle-strip').click();
    await expect(strip).toBeVisible();
  });

  await test.step('an unknown stage is a screen inside the book', async () => {
    await page.goto(`${bookPath}/stages/ocr`);
    await expect(page.getByText('This book has no such stage.')).toBeVisible();
    await expect(page.getByTestId('stage-bar')).toBeVisible();
  });

  await test.step('the shortcuts open on the question mark', async () => {
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await page.keyboard.press('?');
    await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

  await test.step('the activity of the book lists the import that ran', async () => {
    await page.getByTestId('activity-chip').click();
    // The import queues a run of the Split stage and its collection after itself, so it is not the newest job
    const imported = page.getByTestId('activity-job').filter({ hasText: 'Import' });
    await expect(imported.first()).toHaveAttribute('data-state', 'succeeded');
    await page.keyboard.press('Escape');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
