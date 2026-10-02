import { expect, type Page, test } from '@playwright/test';
import { addPlaceholderPages, createBook, registerAndSignIn } from './support/account';

/**
 * The grid of the Order stage on a book of a thousand pages: only the rows in sight are in the document while it
 * scrolls from the first page to the last, a page held with the mouse scrolls the grid by itself and lands where it
 * is let go, and a jump to a place far from the view brings it into sight.
 *
 * The thousand pages are placeholders added through the pages route, since importing a thousand scans would spend the
 * run on the import rather than on the grid.
 */

const LONG_BOOK_PAGES = 1000;
// Far more than a grid shows at once, and far fewer than the book has
const MOST_TILES_IN_DOCUMENT = 60;
const SCROLL_STEPS = 10;
const LONG_BOOK_TIMEOUT_MS = 180_000;
// Pixels the pointer travels before a press is a drag, and from the edge of the grid where it scrolls
const DRAG_START_PX = 12;
const EDGE_MARGIN_PX = 8;
// How far the grid must have scrolled by itself for the held page to be far from where it was lifted
const SCROLLED_FAR_PX = 1500;

/** The position `#n` a tile of the grid shows, or 0 when it shows none. */
function positionOf(text: string | null): number {
  return Number(/#(\d+)/.exec(text ?? '')?.[1] ?? 0);
}

async function openLongBook(page: Page): Promise<void> {
  await registerAndSignIn(page);
  await createBook(page, 'A long book');
  await addPlaceholderPages(page, LONG_BOOK_PAGES, 'page-order');
  await expect(page.getByTestId('order-grid')).toBeVisible();
}

test.describe('a book of a thousand pages in the Order grid', () => {
  test.setTimeout(LONG_BOOK_TIMEOUT_MS);

  test('keeps only the pages in sight in the document and reaches the last page', async ({
    page,
  }) => {
    const tiles = page.getByTestId('order-tile');
    const scroller = page.getByTestId('order-scroll');
    await openLongBook(page);

    await test.step('the grid draws only the start of the book', async () => {
      await expect(tiles.first()).toContainText('#1');
      expect(await tiles.count()).toBeLessThanOrEqual(MOST_TILES_IN_DOCUMENT);
    });

    await test.step('scrolling to the end keeps the document as small as at the start', async () => {
      for (let step = 1; step <= SCROLL_STEPS; step += 1) {
        await scroller.evaluate((element, fraction) => {
          element.scrollTop = (element.scrollHeight - element.clientHeight) * fraction;
        }, step / SCROLL_STEPS);
        await expect(tiles.last()).toBeVisible();
        expect(await tiles.count()).toBeLessThanOrEqual(MOST_TILES_IN_DOCUMENT);
      }
      await expect(tiles.last()).toContainText(`#${LONG_BOOK_PAGES}`);
    });
  });

  test('scrolls while a page is held and drops it where it is let go', async ({ page }) => {
    const tiles = page.getByTestId('order-tile');
    const scroller = page.getByTestId('order-scroll');
    await openLongBook(page);
    await expect(tiles.first()).toContainText('#1');

    const first = await tiles.first().boundingBox();
    const area = await scroller.boundingBox();
    if (first === null || area === null) {
      throw new Error('The grid has no box');
    }
    const tilesAtStart = await tiles.count();
    const heldId = await tiles.first().getAttribute('data-page-id');
    const centre = { x: area.x + area.width / 2, y: area.y + area.height / 2 };

    await test.step('a page held at the lower edge makes the grid scroll', async () => {
      await page.mouse.move(first.x + first.width / 2, first.y + first.height / 2);
      await page.mouse.down();
      await page.mouse.move(first.x + first.width / 2 + DRAG_START_PX, first.y + first.height / 2);
      await page.mouse.move(centre.x, area.y + area.height - EDGE_MARGIN_PX, { steps: 10 });
      await expect
        .poll(() => scroller.evaluate((element) => element.scrollTop))
        .toBeGreaterThan(SCROLLED_FAR_PX);
      expect(await tiles.count()).toBeLessThanOrEqual(MOST_TILES_IN_DOCUMENT);
    });

    let landedAfter = 0;
    await test.step('letting go over a tile far from the start puts the page after it', async () => {
      // Back to the middle of the grid, out of the zone where it scrolls, and let go over the tile that is there
      await page.mouse.move(centre.x, centre.y, { steps: 10 });
      await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
      // The page held under the pointer covers the tile, so the tile is found by its box and not by the pointer
      const overText = await tiles.evaluateAll((all, point) => {
        const hit = all.find((tile) => {
          const box = tile.getBoundingClientRect();
          return (
            box.left <= point.x &&
            point.x <= box.right &&
            box.top <= point.y &&
            point.y <= box.bottom
          );
        });
        return hit?.textContent ?? null;
      }, centre);
      landedAfter = positionOf(overText);
      expect(landedAfter).toBeGreaterThan(tilesAtStart);
      await page.mouse.up();
    });

    await test.step('the held page stands where the tile it was dropped on stood', async () => {
      const moved = page.locator(`[data-testid="order-tile"][data-page-id="${heldId}"]`);
      await expect(page.getByTestId('order-grid')).toHaveAttribute('aria-busy', 'false');
      await expect(moved).toContainText(`#${landedAfter}`);
    });
  });

  test('scrolls to a place to check that is far from the view', async ({ page }) => {
    const tiles = page.getByTestId('order-tile');
    const scroller = page.getByTestId('order-scroll');
    await openLongBook(page);

    await expect(page.getByTestId('places-to-check')).toBeVisible();

    await test.step('the end of the book is far from the first place', async () => {
      await scroller.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      await expect(tiles.last()).toContainText(`#${LONG_BOOK_PAGES}`);
      await expect(tiles.first()).not.toContainText('#1');
    });

    await test.step('Show brings the first place back into sight though its row left the document', async () => {
      await page.getByTestId('places-to-check').getByRole('button').click();
      await expect(tiles.first()).toContainText('#1');
      await expect(tiles.first()).toBeInViewport();
    });
  });
});
