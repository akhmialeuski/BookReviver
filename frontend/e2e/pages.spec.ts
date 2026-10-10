import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openImportStage,
  openOrderStage,
  registerAndSignIn,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * Arranging the pages of a book: open a page from the grid, and a scan and a file from the Import stage, move a page
 * from the viewer, move groups from the panel and the pages of a file that the Import stage selects on the Order
 * stage, edit and number pages, add a missing page and bind a scan to it, delete a page and a file, read a refusal of
 * the server, and see a change made in another tab arrive without a reload. Dragging, spreads, gaps and the preview of
 * a numbering are in `order.spec.ts`.
 */

const PAGES = 5;
const IMAGE_TIMEOUT_MS = 30_000;
const SCENARIO_TIMEOUT_MS = 180_000;

/** The ids of the tiles of the grid in the order they stand. */
async function tileOrder(page: Page): Promise<string[]> {
  return page
    .getByTestId('order-tile')
    .evaluateAll((tiles) => tiles.map((tile) => tile.getAttribute('data-page-id') ?? ''));
}

function tile(page: Page, id: string) {
  return page.locator(`[data-testid="order-tile"][data-page-id="${id}"]`);
}

/** The row of a file on the Import stage, found by its name. */
function sourceRow(page: Page, name: string) {
  return page.getByTestId('source-row').filter({ hasText: name });
}

/** Choose the page a move goes next to, on the strip of thumbnails of the move dialog. */
function anchorTile(page: Page, id: string) {
  return page.locator(`[data-testid="move-strip-page"][data-page-id="${id}"]`);
}

test('a reader arranges the pages of a book', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const tiles = page.getByTestId('order-tile');
  const caption = page.getByTestId('viewer-caption');
  let ids: string[] = [];

  await test.step('upload five pages and see them in the grid', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to arrange');
    await uploadFolder(page, folder, PAGES);
    await openOrderStage(page);
    await expect(tiles).toHaveCount(PAGES);
    ids = await tileOrder(page);
    for (const id of ids) {
      await expect(tile(page, id).getByText('Text', { exact: true })).toBeVisible();
    }
    for (const [index, id] of ids.entries()) {
      await expect(tile(page, id)).toContainText(`#${index + 1}`);
    }
  });

  await test.step('open a page from the grid, from a scan and from a file', async () => {
    await tile(page, ids[2] ?? '').dblclick();
    await expect(page).toHaveURL(new RegExp(`viewer\\?page=${ids[2]}`));
    await expect(caption).toContainText('3 of 5');

    await page.goBack();
    await expect(tiles).toHaveCount(PAGES);
    // The scans and the files are on the Import stage, where a scan opens large in place
    await openImportStage(page);
    await sourceRow(page, 'book/page-02.png').click();
    await page.getByTestId('scan-tile').first().click();
    await expect(page).toHaveURL(/[?&]scan=/);
    // The large scan takes the place of the list, and the address brings the list back
    await page.goBack();
    await expect(sourceRow(page, 'book/page-04.png')).toBeVisible();

    // A file leads to its pages, selected on the Order stage
    await sourceRow(page, 'book/page-04.png').click();
    await page.getByRole('link', { name: 'Show its pages in Order' }).click();
    await expect(page).toHaveURL(/stages\/page-order\?.*source=/);
    await expect(tile(page, ids[3] ?? '')).toHaveAttribute('data-selected', 'true');

    const book = new URL(page.url()).pathname.replace(/\/stages\/.*$/, '');
    await page.goto(`${book}/viewer?page=${ids[3]}`);
    await expect(caption).toContainText('4 of 5');
  });

  await test.step('move a page from the viewer, and keep the link to it', async () => {
    await page.getByRole('button', { name: 'Move page 4' }).click();
    await expect(page.getByRole('heading', { name: 'Move 1 page' })).toBeVisible();
    await anchorTile(page, ids[4] ?? '').click();
    await page.getByRole('radio', { name: 'After' }).check();
    // The dialog says how the book will read, with the moved page set apart, and names the move in its button
    await expect(page.getByTestId('move-reading').getByRole('listitem')).toHaveText([
      '#3',
      '#5',
      '#4',
    ]);
    await page.getByRole('button', { name: 'Move after #5' }).click();
    // The page keeps its id, so the address still names it, and it now stands last
    await expect(page).toHaveURL(new RegExp(`viewer\\?page=${ids[3]}`));
    await expect(caption).toContainText('5 of 5');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');

    // Back to the book opens the stage it was left on, and the grid is on the Order stage
    await page.getByRole('link', { name: 'Back to the book' }).click();
    await openOrderStage(page);
    await expect.poll(() => tileOrder(page)).toEqual([ids[0], ids[1], ids[2], ids[4], ids[3]]);
  });

  await test.step('move a group of pages after another page', async () => {
    await tile(page, ids[0] ?? '').click();
    await tile(page, ids[1] ?? '').click({ modifiers: ['ControlOrMeta'] });
    await expect(page.getByTestId('panel-page')).toContainText('2 pages selected');
    await page.getByRole('button', { name: 'Move to another place…' }).click();
    await expect(page.getByRole('heading', { name: 'Move 2 pages' })).toBeVisible();
    // The pages that move are on the strip but cannot be chosen, since a place next to oneself is not defined
    await expect(anchorTile(page, ids[0] ?? '')).toBeDisabled();
    await expect(anchorTile(page, ids[1] ?? '')).toBeDisabled();
    await anchorTile(page, ids[3] ?? '').click();
    await page.getByRole('button', { name: 'Move after #5' }).click();
    // A refusal of the server keeps the dialog open, so it fails on this line and not in the order below
    await expect(page.getByRole('heading', { name: 'Move 2 pages' })).toBeHidden();
    await expect.poll(() => tileOrder(page)).toEqual([ids[2], ids[4], ids[3], ids[0], ids[1]]);
    await page.getByRole('button', { name: 'Clear the selection' }).click();
  });

  await test.step('read the refusal of the server when a move conflicts', async () => {
    await page.route('**/api/v1/projects/*/pages/move', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          detail: 'Another move took that place first.',
        }),
      }),
    );
    await tile(page, ids[0] ?? '').click();
    await page.getByRole('button', { name: 'Move to another place…' }).click();
    await page.getByRole('button', { name: /^Move after/ }).click();
    await expect(page.getByRole('dialog')).toContainText('Nothing was changed');
    await expect(page.getByRole('dialog')).toContainText('Another move took that place first.');
    await page.unroute('**/api/v1/projects/*/pages/move');
    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('button', { name: 'Clear the selection' }).click();
    // The order is the one the server has, which the refused move did not change
    await expect.poll(() => tileOrder(page)).toEqual([ids[2], ids[4], ids[3], ids[0], ids[1]]);
  });

  await test.step('edit the number, kind, inclusion and notes of a page', async () => {
    const edited = tile(page, ids[2] ?? '');
    await edited.click();
    await page.getByLabel('Printed number').fill('iv');
    await page.getByLabel('Printed number').blur();
    await expect(edited).toContainText('p. iv');
    await page.getByLabel('What these pages are').selectOption('plate');
    await expect(edited.getByText('Plate', { exact: true })).toBeVisible();
    await page.getByLabel('Part of the book').uncheck();
    await expect(edited).toContainText('Left out of the book');
    await page.getByLabel('Notes').fill('A colour plate');
    // The save waits its turn behind the changes before it, so the reload below waits for its answer
    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === 'PATCH' &&
        (response.request().postData() ?? '').includes('A colour plate'),
    );
    await page.getByLabel('Notes').blur();
    await saved;

    // The notes are saved with the other fields
    await page.reload();
    await tile(page, ids[2] ?? '').click();
    await expect(page.getByLabel('Notes')).toHaveValue('A colour plate');
    await page.getByRole('button', { name: 'Clear the selection' }).click();
  });

  await test.step('number the pages of the book, leaving the plate and the excluded page alone', async () => {
    await tile(page, ids[2] ?? '').click();
    await page.getByRole('button', { name: 'Number from here…' }).click();
    await expect(page.getByTestId('new-number')).toHaveCount(4);
    await page.getByRole('button', { name: 'Apply numbers' }).click();
    await expect(page.getByTestId('numbering-panel')).toHaveCount(0);
    await expect(tiles.nth(0)).toContainText('p. iv');
    for (const [index, number] of ['1', '2', '3', '4'].entries()) {
      await expect(tiles.nth(index + 1)).toContainText(`p. ${number}`);
    }
  });

  await test.step('add a missing page at the end of the book', async () => {
    await page.getByRole('button', { name: 'Insert', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Missing page at the end' }).click();
    await expect(tiles).toHaveCount(PAGES + 1);
    const added = tiles.last();
    await expect(added).toContainText('Missing page');
    await added.click();
    await page.getByLabel('Printed number').fill('i');
    await page.getByLabel('Printed number').blur();
    await expect(added).toContainText('p. i');
  });

  await test.step('bind a scan to the missing page by taking it over', async () => {
    await page.getByRole('button', { name: 'Attach a scan…' }).click();
    await page.getByRole('button', { name: 'Scan 1', exact: true }).click();
    await page.getByLabel('Take the scan from the page that shows it now').check();
    await page.getByRole('button', { name: 'Bind scan' }).click();
    // The page that showed the scan is gone, and the missing page became a page of its own
    await expect(tiles).toHaveCount(PAGES);
    await expect(page.getByText('Missing page', { exact: true })).toHaveCount(0);
    await expect(tiles.last()).toContainText('p. i');
  });

  await test.step('move every page of a file to the start of the book', async () => {
    // The file's action opens the Order stage with the pages of the file selected, and they move like any selection
    await openImportStage(page);
    await sourceRow(page, 'book/page-02.png').click();
    await page.getByRole('link', { name: 'Put its pages somewhere else…' }).click();
    await expect(page).toHaveURL(/stages\/page-order\?.*source=/);
    await expect(tile(page, ids[1] ?? '')).toHaveAttribute('data-selected', 'true');
    await page.getByRole('button', { name: 'Move to another place…' }).click();
    await page.getByTestId('move-strip-page').first().click();
    await page.getByRole('radio', { name: 'Before' }).check();
    await page.getByRole('button', { name: /^Move before/ }).click();
    await expect.poll(async () => (await tileOrder(page))[0]).toBe(ids[1]);
  });

  await test.step('delete a file after the confirmation, and keep its page', async () => {
    await openImportStage(page);
    await sourceRow(page, 'book/page-03.png').click();
    await page.getByRole('button', { name: 'Delete this file…' }).click();
    await expect(page.getByText('Delete this file?')).toBeVisible();
    await page.getByRole('button', { name: 'Delete file' }).click();
    await expect(page.getByTestId('source-name')).toHaveCount(PAGES - 1);
    await openOrderStage(page);
    await expect(tiles).toHaveCount(PAGES);
  });

  await test.step('delete a page after the confirmation', async () => {
    await tiles.last().click();
    await page.getByRole('button', { name: 'Delete 1 page…' }).click();
    await expect(page.getByRole('heading', { name: 'Delete 1 page?' })).toBeVisible();
    await page.getByRole('button', { name: 'Delete 1 page', exact: true }).click();
    await expect(tiles).toHaveCount(PAGES - 1);
  });

  await test.step('see a change made in another tab arrive without a reload', async () => {
    const other = await page.context().newPage();
    await other.goto(page.url());
    await expect(other.getByTestId('order-tile')).toHaveCount(PAGES - 1);
    await other.getByTestId('order-tile').first().click();
    await other.getByLabel('Printed number').fill('live');
    await other.getByLabel('Printed number').blur();

    await expect(tiles.first()).toContainText('p. live');
    await other.close();
  });

  await test.step('add a blank leaf and watch its image appear when the job has written it', async () => {
    await page.getByRole('button', { name: 'Insert', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Blank leaf at the end' }).click();
    await expect(tiles).toHaveCount(PAGES);

    const leaf = tiles.last();
    await expect(leaf).toContainText('Blank');
    // A job writes the white image, and the event of its page version makes the grid read the manifest again
    await expect(leaf.locator('img')).toBeVisible({ timeout: IMAGE_TIMEOUT_MS });
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
