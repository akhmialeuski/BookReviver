import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openOrderStage,
  openProjectId,
  registerAndSignIn,
  setKind,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writeSheetsFolder,
} from './support/account';

/**
 * A blank scan replaced by a leaf: a page of kind Blank gets the choice of its image in the panel of the Order stage,
 * the leaf is drawn by a job in place of the scan, the button writes the choice to every Blank page, and choosing the
 * scan again brings it back.
 */

const PAGES = 4;
const SCENARIO_TIMEOUT_MS = 180_000;
const LEAF_TIMEOUT_MS = 60_000;
// The first two pages after the first are blank, by position from zero
const BLANK_POSITIONS = [1, 2];
// The part of the address of an image that says the page order made it, which is where a leaf is made
const LEAF_STAGE = '/page-order/';

interface ListedPage {
  id: string;
  position: number;
  blank_fill: string;
  images: { full: string; thumbnail: string } | null;
}

/** Read the pages of the open book as the API lists them. */
async function listPages(page: Page): Promise<ListedPage[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  return ((await listed.json()) as { items: ListedPage[] }).items;
}

/**
 * Wait until the pages at the positions show a leaf of their choice, or their scan again.
 *
 * @param choice What the pages were given, which the manifest names as `blank_fill`.
 * @param leaf Whether the pages show a leaf, which a job draws a moment after the choice, or their scan.
 */
async function waitForImages(
  page: Page,
  positions: readonly number[],
  choice: string,
  leaf: boolean,
): Promise<void> {
  await expect
    .poll(
      async () =>
        (await listPages(page))
          .filter((entry) => positions.includes(entry.position))
          .every(
            (entry) =>
              entry.blank_fill === choice &&
              entry.images !== null &&
              entry.images.full.includes(LEAF_STAGE) === leaf,
          ),
      { timeout: LEAF_TIMEOUT_MS },
    )
    .toBe(true);
}

test('a blank scan gets a leaf of the paper of the book in its place, and the scan comes back', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(PAGES);
  const tiles = page.getByTestId('order-tile');
  const block = page.getByTestId('blank-leaf');
  // What the grid drew for each blank page before it had a leaf, read from the manifest of the book
  const scanThumbnails = new Map<number, string>();

  await test.step('two pages of the book are blank, and the panel offers the image of a blank page', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with blank leaves');
    await uploadFolder(page, folder, PAGES);
    for (const position of BLANK_POSITIONS) {
      await setKind(page, position, 'blank');
    }
    await openOrderStage(page);
    await expect(tiles).toHaveCount(PAGES);
    for (const entry of await listPages(page)) {
      if (BLANK_POSITIONS.includes(entry.position)) {
        scanThumbnails.set(entry.position, entry.images?.thumbnail ?? '');
      }
    }
    expect([...scanThumbnails.values()].every((thumbnail) => thumbnail !== '')).toBe(true);
    await tiles.nth(1).click();
    await expect(block).toBeVisible();
    await expect(block.getByRole('radio', { name: 'Keep the scan' })).toBeChecked();
    // A page of text has no such choice
    await tiles.nth(0).click();
    await expect(block).toHaveCount(0);
    await tiles.nth(1).click();
    await snap(page, 'blank-leaf-choice');
  });

  await test.step('a white leaf replaces the scan of the selected page only', async () => {
    await block.getByRole('radio', { name: 'White leaf' }).click();
    await expect(block.getByRole('radio', { name: 'White leaf' })).toBeChecked();
    await waitForImages(page, [1], 'white', true);
    const others = (await listPages(page)).filter((entry) => entry.position === 2);
    expect(others.map((entry) => entry.blank_fill)).toEqual(['scan']);
  });

  await test.step('the button gives every blank page a leaf of the paper of the book', async () => {
    await block.getByRole('radio', { name: 'Paper of the book' }).click();
    await block.getByRole('button', { name: 'Apply to all 2 Blank pages' }).click();
    await waitForImages(page, BLANK_POSITIONS, 'paper', true);
    await waitForIdleJobs(page, openProjectId(page));
    await expect(tiles.nth(2)).toContainText('Blank');
    // The grid itself draws the leaf of every blank page, with no reload of the screen
    const listed = await listPages(page);
    for (const position of BLANK_POSITIONS) {
      const leaf = listed.find((entry) => entry.position === position)?.images?.thumbnail ?? '';
      expect(leaf).not.toBe('');
      expect(leaf).not.toBe(scanThumbnails.get(position));
      await expect(tiles.nth(position).locator('img')).toHaveAttribute('src', leaf);
    }
    await snap(page, 'blank-leaf-paper');
  });

  await test.step('the scan comes back with the choice of the scan', async () => {
    // The radio shows the fill of the manifest, which the request changes a moment after the click
    await block.getByRole('radio', { name: 'Keep the scan' }).click();
    await waitForImages(page, [1], 'scan', false);
    await expect(block.getByRole('radio', { name: 'Keep the scan' })).toBeChecked();
    await snap(page, 'blank-leaf-scan-back');
  });

  await test.step('a page that stops being blank gets its scan back and the panel says so', async () => {
    await tiles.nth(2).click();
    await expect(block.getByRole('radio', { name: 'Paper of the book' })).toBeChecked();
    await page.getByLabel('What these pages are').selectOption('text');
    await expect(page.getByTestId('leaf-replaced')).toContainText('now shows the scan');
    await expect(block).toHaveCount(0);
    await waitForImages(page, [2], 'scan', false);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
