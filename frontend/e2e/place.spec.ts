import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  addPlaceholderPages,
  createBook,
  deleteAsReader,
  openProjectId,
  readPlace,
  registerAndSignIn,
  signIn,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * A book opens where it was left: the stage, the page, the layout, the filter and the zoom of the canvas, or the
 * reading mode on its page, on any device of the account and for no other account. The place is written about a second
 * after the last move, and at once when the page is closed.
 */

const PAGES = 6;
const LONG_BOOK_PAGES = 150;
const LONG_BOOK_TIMEOUT_MS = 180_000;
const NOT_FOUND = 404;
const NO_CONTENT = 204;
const NO_ID = '';
// The strip shows the first page in sight within a row or two of its top edge
const MOST_PX_BELOW_TOP = 260;
const MOST_ZOOM_OUT = 0.9;

/** Open the book from the library by its card, the way a reader comes back to it. */
async function openFromLibrary(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Library', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your books' })).toBeVisible();
  await page.getByTestId('project-card').first().click();
}

/** Read the zoom the canvas reports once it has stopped moving. */
async function zoomOf(page: Page): Promise<number> {
  const text = await page.getByTestId('viewer-canvas').getAttribute('data-zoom');
  return Number(text);
}

test('a reader returns to the stage, the page, the layout and the zoom of a book', async ({
  page,
  browser,
}) => {
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const caption = page.getByTestId('canvas-caption');
  let book = '';
  let projectId = '';
  let address = '';
  let zoom = 0;
  let email = '';

  await test.step('a book is worked on at a stage, a page, a layout, a filter and a zoom', async () => {
    email = await registerAndSignIn(page);
    await createBook(page, 'A book to resume');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    book = `/projects/${projectId}`;

    // The spread draws pages on the plain canvas, which the zoom is kept for
    await page.goto(`${book}/stages/geometry`);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await page.getByTestId('strip-page').nth(2).click();
    await expect(caption).toContainText('3 of 6');
    await page.getByTestId('canvas-spread').click();
    await expect(page).toHaveURL(/view=spread/);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await page.getByTestId('strip-filter-check').click();
    await expect(page).toHaveURL(/filter=check/);

    // The pages are tiny images, which the canvas will not enlarge, so the zoom that moves is the one out
    const fitted = await zoomOf(page);
    await page.getByRole('button', { name: 'Zoom out' }).click();
    await expect.poll(() => zoomOf(page)).toBeLessThan(fitted * MOST_ZOOM_OUT);
    zoom = await zoomOf(page);
    address = new URL(page.url()).pathname + new URL(page.url()).search;
  });

  await test.step('the server holds the place a second after the last move', async () => {
    await expect
      .poll(async () => (await readPlace(page, projectId)).place)
      .toMatchObject({
        mode: 'workspace',
        stage: 'geometry',
        view: 'spread',
        filter: 'check',
        canvas: expect.objectContaining({ zoom: expect.closeTo(zoom, 1) }),
      });
  });

  await test.step('the book opens from the library at the same place', async () => {
    await openFromLibrary(page);
    await expect(page).toHaveURL(new RegExp(`${address.replace('?', '\\?')}$`));
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(caption).toContainText('2 of 6');
    await expect(caption).toContainText('3 of 6');
    await expect(page.getByTestId('strip-filter-check')).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => zoomOf(page)).toBeCloseTo(zoom, 1);
  });

  await test.step('a second browser of the same account opens the book at the same place', async () => {
    const other = await browser.newContext();
    const second = await other.newPage();
    await signIn(second, email);
    await second.goto(book);
    await expect(second).toHaveURL(new RegExp(`${address.replace('?', '\\?')}$`));
    await expect(second.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect.poll(() => zoomOf(second)).toBeCloseTo(zoom, 1);
    await other.close();
  });

  await test.step('another account does not see the place, and the book is not found for it', async () => {
    const stranger = await browser.newContext();
    const strangerPage = await stranger.newPage();
    await registerAndSignIn(strangerPage);
    expect((await readPlace(strangerPage, projectId)).status).toBe(NOT_FOUND);
    await stranger.close();
  });

  await test.step('a page turned just before the tab closes is not lost', async () => {
    await page.getByRole('button', { name: 'Next page' }).click();
    await expect(caption).toContainText('4 of 6');
    await expect(caption).toContainText('5 of 6');
    const turned = (await canvas.getAttribute('data-page-ids'))?.split(',')[0] ?? NO_ID;
    expect(turned).not.toBe(NO_ID);
    // Leaving at once is before the pause is over, so only the write on hiding the page can save it
    await page.goto('about:blank');
    await expect.poll(async () => (await readPlace(page, projectId)).place?.page_id).toBe(turned);
  });

  await test.step('a page that was deleted opens the same stage on its first page', async () => {
    await page.goto(book);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    const open = (await canvas.getAttribute('data-page-ids'))?.split(',')[0] ?? NO_ID;
    await expect.poll(async () => (await readPlace(page, projectId)).place?.page_id).toBe(open);

    expect(await deleteAsReader(page, `/api/v1/projects/${projectId}/pages/${open}`)).toBe(
      NO_CONTENT,
    );
    await page.goto(book);
    await expect(page).toHaveURL(/\/stages\/geometry/);
    await expect(page).not.toHaveURL(new RegExp(`page=${open}`));
    await expect(page.getByTestId('stage-screen')).toBeVisible();
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(caption).toContainText('1 of 5');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('a book that was closed in the reading mode opens in it on the same page', async ({
  page,
}) => {
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const caption = page.getByTestId('viewer-caption');
  let projectId = '';

  await test.step('a book is read to its fourth page after work on a stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to read again');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    await page.goto(`/projects/${projectId}/stages/geometry`);
    await expect(page.getByTestId('stage-screen')).toHaveAttribute('data-stage', 'geometry');
    await page.getByRole('link', { name: 'Read the book' }).click();
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    for (const position of [2, 3, 4]) {
      await page.getByRole('button', { name: 'Next page' }).click();
      await expect(caption).toContainText(`${position} of 6`);
    }
    await expect
      .poll(async () => (await readPlace(page, projectId)).place)
      .toMatchObject({ mode: 'reading', stage: 'geometry' });
  });

  await test.step('the book opens from the library in the reading mode on that page', async () => {
    await page.getByRole('link', { name: 'Back to the book' }).click();
    await expect(page).toHaveURL(/\/stages\/geometry/);
    await page.getByRole('link', { name: 'Read the book' }).click();
    await expect(caption).toContainText('1 of 6');
    await page.getByRole('button', { name: 'Last page' }).click();
    await expect(caption).toContainText('6 of 6');
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    const last = (await canvas.getAttribute('data-page-ids')) ?? NO_ID;
    await expect.poll(async () => (await readPlace(page, projectId)).place?.page_id).toBe(last);
    await openFromLibrary(page);
    await expect(page).toHaveURL(new RegExp(`/viewer\\?page=${last}$`));
    await expect(caption).toContainText('6 of 6');
    await expect(canvas).toHaveAttribute('data-state', 'ready');
  });

  await test.step('the way back from reading leads to the stage the reader came from', async () => {
    await page.getByRole('link', { name: 'Back to the book' }).click();
    await expect(page).toHaveURL(/\/stages\/geometry/);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('the strip of a long book returns to the page that was first in sight', async ({ page }) => {
  test.setTimeout(LONG_BOOK_TIMEOUT_MS);
  const scroller = page.getByTestId('strip-scroll');
  let projectId = '';
  let first = '';

  await test.step('a long book is scrolled down its strip', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A long book to resume');
    projectId = openProjectId(page);
    await addPlaceholderPages(page, LONG_BOOK_PAGES, 'geometry');
    await expect(page.getByTestId('strip-page').first()).toBeVisible();
    const top =
      (await page.getByTestId('strip-page').first().getAttribute('data-page-id')) ?? NO_ID;
    await scroller.evaluate((element) => {
      element.scrollTop = (element.scrollHeight - element.clientHeight) * 0.6;
    });
    await expect
      .poll(async () => (await readPlace(page, projectId)).place?.strip_page_id ?? top)
      .not.toBe(top);
    first = (await readPlace(page, projectId)).place?.strip_page_id ?? NO_ID;
    expect(first).not.toBe(NO_ID);
  });

  await test.step('the book opens with that page at the top of the strip', async () => {
    await openFromLibrary(page);
    await expect(page.getByTestId('strip-page').first()).toBeVisible();
    const tile = page.locator(`[data-testid="strip-page"][data-page-id="${first}"]`);
    await expect(tile).toBeVisible();
    await expect
      .poll(async () => {
        const below = await tile.evaluate((element) => {
          const strip = element.closest('[data-testid="strip-scroll"]');
          return strip === null
            ? -1
            : element.getBoundingClientRect().top - strip.getBoundingClientRect().top;
        });
        return below >= -MOST_PX_BELOW_TOP && below <= MOST_PX_BELOW_TOP;
      })
      .toBe(true);
  });
});

test('the zoom of the canvas of a processing stage comes back with the book', async ({ page }) => {
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  let projectId = '';
  let zoom = 0;

  await test.step('a page of the Geometry stage is zoomed out', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with a zoom');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    await page.goto(`/projects/${projectId}/stages/geometry`);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect.poll(() => zoomOf(page)).toBeGreaterThan(0);
    const fitted = await zoomOf(page);
    await page.getByRole('button', { name: 'Zoom out' }).click();
    await expect.poll(() => zoomOf(page)).toBeLessThan(fitted * MOST_ZOOM_OUT);
    zoom = await zoomOf(page);
    await expect
      .poll(async () => (await readPlace(page, projectId)).place?.canvas?.zoom ?? fitted)
      .toBeCloseTo(zoom, 1);
  });

  await test.step('the stage opens from the library at the same zoom', async () => {
    await openFromLibrary(page);
    await expect(page).toHaveURL(/\/stages\/geometry/);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect.poll(() => zoomOf(page)).toBeCloseTo(zoom, 1);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
