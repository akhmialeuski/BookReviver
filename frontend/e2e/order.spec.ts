import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openOrderStage,
  registerAndSignIn,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * The Order stage: a grid of the pages of the book that is selected with the click, Shift and Ctrl, rearranged by
 * dragging with the mouse and with the keyboard, shown in spreads, numbered with a preview from the server, checked
 * for gaps in the printed numbers, and added to and deleted from.
 */

const PAGES = 6;
const SCENARIO_TIMEOUT_MS = 180_000;
const SAME_PLACE = 5;

/** The ids of the tiles of the grid in the order they stand, which is the order of the book. */
async function tileIds(page: Page): Promise<string[]> {
  return page
    .getByTestId('order-tile')
    .evaluateAll((tiles) => tiles.map((tile) => tile.getAttribute('data-page-id') ?? ''));
}

function tile(page: Page, id: string) {
  return page.locator(`[data-testid="order-tile"][data-page-id="${id}"]`);
}

function projectIdOf(page: Page): string {
  const id = new URL(page.url()).pathname.match(/\/projects\/([^/]+)/)?.[1];
  if (id === undefined) {
    throw new Error(`The address ${page.url()} names no book`);
  }
  return id;
}

/**
 * Wait until the grid is no longer waiting for the server: no change is in flight, the pages are not being read again,
 * and the labels of a numbering are not being worked out. Until then it may show the state before the last change.
 */
async function settled(page: Page): Promise<void> {
  await expect(page.getByTestId('order-grid')).toHaveAttribute('aria-busy', 'false');
}

/**
 * Pick the focused tile up with the space bar, and wait for the announcement that follows the lift: the held page is
 * over itself, which is no place.
 *
 * dnd-kit starts to listen for the arrow keys a moment after the key that lifts, so an arrow pressed in the same
 * instant is lost, which a person never does.
 */
async function pickUp(page: Page): Promise<void> {
  await page.keyboard.press('Space');
  await expect(page.locator('[aria-live="assertive"]')).toHaveText(
    'Over the pages being moved, which is no place.',
  );
}

/**
 * Press the right arrow while a page is held, and wait for the announcement that says where the held page is now.
 *
 * dnd-kit works the next move out from the render of the last one, so keys pressed faster than a person presses them
 * can start from the old place. The announcement is what a screen reader hears, and it comes after the move.
 */
async function arrowRight(page: Page, announcement: string): Promise<void> {
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('[aria-live="assertive"]')).toHaveText(announcement);
}

/** Sign in, make a book of `PAGES` pages and open its Order stage. */
async function openBook(page: Page, title: string): Promise<string> {
  const folder = await writePagesFolder(PAGES);
  await registerAndSignIn(page);
  await createBook(page, title);
  await uploadFolder(page, folder, PAGES);
  await openOrderStage(page);
  await expect(page.getByTestId('order-tile')).toHaveCount(PAGES);
  return folder;
}

/** Write the printed number of one page from the panel, which saves when the field is left. */
async function labelPage(page: Page, id: string, label: string): Promise<void> {
  await tile(page, id).click();
  await page.getByLabel('Printed number').fill(label);
  await page.getByLabel('Printed number').blur();
  await expect(tile(page, id)).toContainText(label === '' ? 'no number' : `p. ${label}`);
}

test('a reader selects, shows and drags the pages of a book', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await openBook(page, 'A book to arrange');
  const tiles = page.getByTestId('order-tile');
  const selected = page.getByTestId('selection-heading');
  let ids: string[] = [];

  await test.step('the grid shows every page with its place in the book', async () => {
    ids = await tileIds(page);
    await expect(page.getByTestId('stage-title')).toHaveText('Order');
    await expect(tiles.nth(0)).toContainText('#1');
    await expect(tiles.nth(5)).toContainText('#6');
    await expect(tiles.nth(0)).toContainText('no number');
    await expect(tiles.nth(0)).toContainText('Text');
  });

  await test.step('the size slider changes the size of the tiles', async () => {
    const before = (await tiles.nth(0).boundingBox())?.width ?? 0;
    await page.getByTestId('tile-size').fill('280');
    await expect
      .poll(async () => (await tiles.nth(0).boundingBox())?.width ?? 0)
      .toBeGreaterThan(before);
  });

  await test.step('a click selects a page, Shift a range and Ctrl one more', async () => {
    await tiles.nth(1).click();
    await expect(tiles.nth(1)).toHaveAttribute('aria-pressed', 'true');
    await expect(selected).toContainText('1 page selected');

    await tiles.nth(3).click({ modifiers: ['Shift'] });
    await expect(selected).toContainText('3 pages selected');
    await expect(selected).toContainText('#2–#4');

    await tiles.nth(5).click({ modifiers: ['ControlOrMeta'] });
    await expect(selected).toContainText('4 pages selected');

    await page.getByRole('button', { name: 'Clear the selection' }).click();
    await expect(page.getByTestId('selection-panel')).toHaveCount(0);
    await expect(tiles.nth(1)).toHaveAttribute('aria-pressed', 'false');
  });

  await test.step('spreads keep the cover alone on the right and pair the pages after it', async () => {
    await page.getByRole('button', { name: 'Spreads' }).click();
    await expect(page).toHaveURL(/view=spread/);
    const groups = page.getByTestId('order-group');
    await expect(groups).toHaveCount(4);
    await expect(groups.nth(0)).toHaveAttribute('data-empty-side', 'left');
    await expect(groups.nth(0).getByTestId('order-tile')).toHaveCount(1);
    await expect(groups.nth(1).getByTestId('order-tile')).toHaveCount(2);
    await expect(groups.nth(3)).toHaveAttribute('data-empty-side', 'right');

    await page.reload();
    await expect(page.getByTestId('order-group')).toHaveCount(4);
    await page.getByRole('button', { name: 'Pages', exact: true }).click();
    await expect(page).not.toHaveURL(/view=spread/);
    await expect(page.getByTestId('order-group')).toHaveCount(PAGES);
  });

  await test.step('the keyboard picks a page up, moves it and drops it', async () => {
    await tiles.nth(0).focus();
    await pickUp(page);
    await arrowRight(page, 'Over #2.');
    await arrowRight(page, 'Over #3.');
    await page.keyboard.press('Space');
    await expect
      .poll(() => tileIds(page))
      .toEqual([ids[1], ids[2], ids[0], ids[3], ids[4], ids[5]]);
    // Dropping did not select the page, as Space is not the key that selects
    await expect(tile(page, ids[0] ?? '')).toHaveAttribute('aria-pressed', 'false');
  });

  await test.step('a selected group travels together, and the stack shows how many pages', async () => {
    // The book reads 1 2 0 3 4 5, so the group of the first two tiles is ids 1 and 2
    await tiles.nth(0).click();
    await tiles.nth(1).click({ modifiers: ['ControlOrMeta'] });
    await expect(selected).toContainText('2 pages selected');

    await tiles.nth(0).focus();
    await pickUp(page);
    await expect(page.getByTestId('drag-count')).toHaveText('2 pages');
    // The second tile is one of the carried pages, which is no place to drop on, and the keyboard says so
    await arrowRight(page, 'Over the pages being moved, which is no place.');
    await arrowRight(page, 'Over #3.');
    await arrowRight(page, 'Over #4.');
    await page.keyboard.press('Space');
    // The group of ids 1 and 2 lands after the page it was dropped on, and keeps its order
    await expect
      .poll(() => tileIds(page))
      .toEqual([ids[0], ids[3], ids[1], ids[2], ids[4], ids[5]]);
    await page.getByRole('button', { name: 'Clear the selection' }).click();
  });

  await test.step('a drag with the mouse draws the bar and moves the page', async () => {
    // The order to start from is the one the server has, after the move before this one has been read back
    await settled(page);
    const order = await tileIds(page);
    const source = tile(page, order[0] ?? '');
    const target = tile(page, order[2] ?? '');
    const from = await source.boundingBox();
    const to = await target.boundingBox();
    if (from === null || to === null) {
      throw new Error('The tiles have no box');
    }
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      from.x + from.width / 2 + SAME_PLACE,
      from.y + from.height / 2 + SAME_PLACE,
    );
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
    await expect(page.getByTestId('drop-bar')).toHaveAttribute('data-side', 'after');
    await page.mouse.up();
    await expect
      .poll(() => tileIds(page))
      .toEqual([order[1], order[2], order[0], ...order.slice(3)]);
  });

  await test.step('a refused move puts the grid back and says why', async () => {
    const before = await tileIds(page);
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
    await tiles.nth(0).focus();
    await pickUp(page);
    await arrowRight(page, 'Over #2.');
    await page.keyboard.press('Space');

    const failure = page.getByTestId('move-error');
    await expect(failure).toContainText('Nothing was changed');
    await expect(failure).toContainText('Another move took that place first.');
    await expect.poll(() => tileIds(page)).toEqual(before);
    await page.unroute('**/api/v1/projects/*/pages/move');
    await page.getByRole('button', { name: 'Dismiss' }).click();
    await expect(failure).toHaveCount(0);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('a reader numbers pages with a preview and finds the pages that are missing', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await openBook(page, 'A book to number');
  const tiles = page.getByTestId('order-tile');
  const ids = await tileIds(page);
  const panel = page.getByTestId('numbering-panel');
  const firstId = ids[0] ?? '';
  const lastId = ids.at(-1) ?? '';

  await test.step('a plate keeps its place and takes no number', async () => {
    await tile(page, ids[1] ?? '').click();
    await page.getByLabel('What these pages are').selectOption('plate');
    await expect(tile(page, ids[1] ?? '')).toContainText('Plate');
  });

  await test.step('the preview shows the new numbers in blue and saves nothing', async () => {
    await page.getByRole('button', { name: 'Number pages' }).click();
    await expect(panel).toBeVisible();
    await page.getByLabel('From', { exact: true }).selectOption(firstId);
    await page.getByLabel('To', { exact: true }).selectOption(lastId);
    await page.getByLabel('First number').fill('10');

    await expect(page.getByTestId('new-number')).toHaveText(['10', '11', '12', '13', '14']);
    await expect(page.getByTestId('numbering-counts')).toHaveText(
      '5 pages get a new number · 1 is skipped',
    );
    await expect(page.getByTestId('numbering-preview-note')).toContainText('Nothing is saved');

    // Nothing was written: the pages still have no number after a reload
    await page.reload();
    await expect(tiles).toHaveCount(PAGES);
    await expect(page.getByTestId('numbering-panel')).toHaveCount(0);
    await expect(tiles.nth(0)).toContainText('no number');
  });

  let shown: string[] = [];
  await test.step('the numbers that are saved are the numbers that were shown', async () => {
    await page.getByRole('button', { name: 'Number pages' }).click();
    await page.getByLabel('From', { exact: true }).selectOption(firstId);
    await page.getByLabel('First number').fill('10');
    // Until the server has answered for this number, the grid still shows the numbers of the one before it
    await settled(page);
    await expect(page.getByTestId('new-number')).toHaveCount(5);
    shown = await page.getByTestId('new-number').allTextContents();

    await page.getByRole('button', { name: 'Apply numbers' }).click();
    await expect(page.getByTestId('numbering-panel')).toHaveCount(0);
    await expect(page.getByTestId('new-number')).toHaveCount(0);
    // The plate is the second page and keeps no number, and the others carry the numbers that were shown
    const numbered = [0, 2, 3, 4, 5];
    for (const [index, number] of shown.entries()) {
      await expect(tiles.nth(numbered[index] ?? 0)).toContainText(`p. ${number}`);
    }
    await expect(tiles.nth(1)).toContainText('no number');
  });

  await test.step('numbers written by hand leave a jump that shows as a card', async () => {
    const labels = ['1', '2', '5', '6', '7', '8'];
    for (const [index, label] of labels.entries()) {
      await labelPage(page, ids[index] ?? '', label);
    }
    // The plate of the second page took a number by hand, so every page now has one
    const card = page.getByTestId('gap-card');
    await expect(card).toHaveCount(1);
    await expect(card).toContainText('p. 3–4 missing?');
    await expect(card).toContainText('The numbers jump from 2 to 5');
    await expect(page.getByTestId('places-to-check')).toContainText('1 place to check.');
    await page.getByRole('button', { name: 'Clear the selection' }).click();
  });

  await test.step('numbering straight through would hide the gap, and says so', async () => {
    await page.getByRole('button', { name: 'Number pages' }).click();
    await page.getByLabel('From', { exact: true }).selectOption(firstId);
    await page.getByLabel('To', { exact: true }).selectOption(lastId);
    await page.getByLabel('First number').fill('1');
    // Every kind is counted, so the plate of the second page takes its number too
    await page.getByRole('checkbox', { name: 'Plate' }).uncheck();

    const warning = page.getByTestId('hides-gap');
    await expect(warning).toContainText('This hides a gap.');
    await expect(warning).toContainText('The printed numbers jump from 2 to 5');

    // Two runs stop the first run before the gap, which leaves the gap where it is
    await page.getByRole('button', { name: 'Number in two runs' }).click();
    await expect(page.getByTestId('hides-gap')).toHaveCount(0);

    await page.getByLabel('To', { exact: true }).selectOption(lastId);
    await expect(warning).toBeVisible();
    // The warning of the numbering before is still shown until the server has answered for this one
    await settled(page);
    await expect(warning).toBeVisible();
    await page.getByRole('button', { name: 'Add the missing pages first' }).click();
    await expect(tiles).toHaveCount(PAGES + 2);
    await expect(page.getByTestId('hides-gap')).toHaveCount(0);
    await page.getByRole('button', { name: 'Cancel' }).click();
  });

  await test.step('the pages that were added stand for the missing numbers', async () => {
    await expect(page.getByTestId('gap-card')).toHaveCount(0);
    await expect(tiles.nth(2)).toContainText('p. 3');
    await expect(tiles.nth(2)).toContainText('Missing page');
    await expect(tiles.nth(3)).toContainText('p. 4');
    await expect(page.getByTestId('places-to-check')).toContainText('2 places to check.');
    await expect(page.getByTestId('places-to-check')).toContainText('2 pages have no scan yet.');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('a reader adds and deletes pages, and follows a link from a file', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await openBook(page, 'A book to extend');
  const tiles = page.getByTestId('order-tile');
  const ids = await tileIds(page);

  await test.step('a blank leaf goes before the selected page', async () => {
    await tile(page, ids[2] ?? '').click();
    await page.getByRole('button', { name: 'Insert', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Blank leaf before' }).click();
    await expect(tiles).toHaveCount(PAGES + 1);
    await settled(page);
    const order = await tileIds(page);
    expect(order.slice(0, 2)).toEqual(ids.slice(0, 2));
    expect(order.slice(3)).toEqual(ids.slice(2));
    await expect(tiles.nth(2)).toContainText('Blank');
  });

  await test.step('a missing page goes at the end, and a scan is attached to it', async () => {
    await page.getByRole('button', { name: 'Insert', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Missing page at the end' }).click();
    await expect(tiles).toHaveCount(PAGES + 2);
    const missing = tiles.last();
    await expect(missing).toContainText('Missing page');
    await missing.click();
    await page.getByRole('button', { name: 'Attach a scan…' }).click();
    await page.getByRole('button', { name: 'Scan 1', exact: true }).click();
    await page.getByLabel('Take the scan from the page that shows it now').check();
    await page.getByRole('button', { name: 'Bind scan' }).click();
    // The page that showed the scan is gone, and the missing page became a page of its own
    await expect(tiles).toHaveCount(PAGES + 1);
    await expect(page.getByText('Missing page', { exact: true })).toHaveCount(0);
  });

  await test.step('the selected pages are deleted after the confirmation', async () => {
    await tiles.nth(0).click();
    await tiles.nth(1).click({ modifiers: ['Shift'] });
    await page.getByRole('button', { name: 'Delete 2 pages…' }).click();
    await expect(page.getByRole('heading', { name: 'Delete 2 pages?' })).toBeVisible();
    await page.getByRole('button', { name: 'Delete 2 pages', exact: true }).click();
    await expect(tiles).toHaveCount(PAGES - 1);
  });

  await test.step('a link from a file opens the stage with the pages of that file selected', async () => {
    const projectId = projectIdOf(page);
    const response = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
    const items: { id: string; source_id: string | null }[] = (await response.json()).items;
    const wanted = items.find((item) => item.source_id !== null);
    expect(wanted).toBeDefined();

    await page.goto(`/projects/${projectId}/stages/page-order?source=${wanted?.source_id}`);
    await expect(page.getByTestId('selection-heading')).toContainText('1 page selected');
    await expect(tile(page, wanted?.id ?? '')).toHaveAttribute('aria-pressed', 'true');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
