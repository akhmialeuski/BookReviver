import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writeMixedFolder,
} from './support/account';
import { readStageRows, runPages, selectPagesInGrid } from './support/page-work';

/**
 * The content type of a page: the program finds text, a colour picture or a black-and-white one on each page as the
 * pages are made, the strip marks every page with it, and the selected pages are changed at once from the menu of the
 * canvas toolbar and given back to the program. A page that was run and is given another content type is out of date, and
 * the strip draws a pencil on the mark of a page whose content type was set by hand.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const DETECT_TIMEOUT_MS = 90_000;
const PAGES = 4;
const TEXT_POSITION = 0;
const COLOUR_POSITION = 1;
const BW_POSITION = 2;
const LAST_POSITION = 3;

// Tall enough for the pictures of the key states to show the marks of the strip and the section of the panel
test.use({ viewport: { width: 1280, height: 1000 } });

interface ListedPage {
  position: number;
  content_type: string;
  content_source: string;
}

/** Read what each page of the open book shows, in book order. */
async function contentOf(page: Page): Promise<string[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: ListedPage[] }).items;
  return items
    .toSorted((a, b) => a.position - b.position)
    .map((item) => `${item.content_type}:${item.content_source}`);
}

test('the program finds what each page shows, the strip marks it, and the selected pages are changed and given back at once', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeMixedFolder();
  const marks = page.getByTestId('strip-content');
  const tiles = page.getByTestId('strip-page');
  // The pencil the strip draws on the mark of a page whose content type was set by hand
  const pencil = (position: number) => marks.nth(position).locator('svg.lucide-pencil');
  const menu = page.getByTestId('content-type-menu');
  const selectedScope = page.getByTestId('content-scope-selected');

  await test.step('the pages made from the scans are detected without being asked', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with plates');
    await uploadFolder(page, folder, PAGES);
    await expect
      .poll(() => contentOf(page), { timeout: DETECT_TIMEOUT_MS })
      .toEqual(['text:detected', 'color-picture:detected', 'bw-picture:detected', 'text:detected']);
    await waitForIdleJobs(page, openProjectId(page));
  });

  await test.step('the strip of the Geometry stage marks each page with what it shows', async () => {
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(marks).toHaveCount(PAGES);
    await expect(marks.nth(TEXT_POSITION)).toHaveAttribute('data-content', 'text');
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-content', 'color-picture');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute('data-content', 'bw-picture');
    await expect(marks.nth(COLOUR_POSITION)).toContainText('Colour');
    await expect(marks.nth(BW_POSITION)).toContainText('B/W');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute(
      'title',
      'Black-and-white picture · Found by the program',
    );
    await snap(page, 'content-type-marks-in-the-strip');
  });

  await test.step('the stage runs on every page, so each page has a result that can go out of date', async () => {
    await runPages(page);
    expect((await readStageRows(page)).map((row) => row.status)).toEqual([
      'fresh',
      'fresh',
      'fresh',
      'fresh',
    ]);
  });

  await test.step('two selected pages of different types are changed to text at once', async () => {
    // The selection stays as the strip comes back, and the menu of the canvas toolbar reaches the selected pages
    await selectPagesInGrid(page, [COLOUR_POSITION, BW_POSITION]);
    await menu.click();
    await expect(selectedScope).toContainText('2');
    await selectedScope.click();
    await expect(selectedScope).toHaveAttribute('aria-checked', 'true');
    await snap(page, 'content-type-two-pages-selected');

    await page.getByTestId('content-type-text').click();
    await expect
      .poll(() => contentOf(page))
      .toEqual(['text:detected', 'text:hand', 'text:hand', 'text:detected']);
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-content', 'text');
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-source', 'hand');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute('data-source', 'hand');
    await snap(page, 'content-type-set-by-hand');
  });

  await test.step('the pages set by hand are given back to the program, which finds the pictures again', async () => {
    await menu.click();
    await expect(selectedScope).toHaveAttribute('aria-checked', 'true');
    await page.getByTestId('content-type-detect').click();
    await expect
      .poll(() => contentOf(page), { timeout: DETECT_TIMEOUT_MS })
      .toEqual(['text:detected', 'color-picture:detected', 'bw-picture:detected', 'text:detected']);
    // The menu stays open while the program works, so it is shut once the pages are found again
    await page.keyboard.press('Escape');
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-source', 'detected');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute('data-content', 'bw-picture');
    await snap(page, 'content-type-detected-again');
  });

  await test.step('two pages of text are changed to Colour picture at once, with a pencil on both marks and both out of date', async () => {
    await selectPagesInGrid(page, [TEXT_POSITION, LAST_POSITION]);
    await expect(pencil(TEXT_POSITION)).toHaveCount(0);
    await expect(pencil(LAST_POSITION)).toHaveCount(0);
    await menu.click();
    await expect(selectedScope).toContainText('2');
    await selectedScope.click();
    await page.getByTestId('content-type-color-picture').click();
    await expect
      .poll(() => contentOf(page))
      .toEqual([
        'color-picture:hand',
        'color-picture:detected',
        'bw-picture:detected',
        'color-picture:hand',
      ]);
    for (const position of [TEXT_POSITION, LAST_POSITION]) {
      await expect(marks.nth(position)).toHaveAttribute('data-content', 'color-picture');
      await expect(pencil(position)).toBeVisible();
      await expect(tiles.nth(position)).toContainText('Out of date');
    }
    // The pages that were not changed have no pencil
    await expect(pencil(COLOUR_POSITION)).toHaveCount(0);
    await expect(pencil(BW_POSITION)).toHaveCount(0);
    const rows = await readStageRows(page);
    expect(rows[TEXT_POSITION]?.status).toBe('stale');
    expect(rows[LAST_POSITION]?.status).toBe('stale');
    await snap(page, 'content-type-colour-picture-by-hand');
  });

  await test.step('Detect again gives the two pages back to the program, which finds text and takes the pencil off', async () => {
    await menu.click();
    await expect(selectedScope).toHaveAttribute('aria-checked', 'true');
    await page.getByTestId('content-type-detect').click();
    await expect
      .poll(() => contentOf(page), { timeout: DETECT_TIMEOUT_MS })
      .toEqual(['text:detected', 'color-picture:detected', 'bw-picture:detected', 'text:detected']);
    // The menu stays open while the program works, so it is shut once the pages are found again
    await page.keyboard.press('Escape');
    await expect(pencil(TEXT_POSITION)).toHaveCount(0);
    await expect(pencil(LAST_POSITION)).toHaveCount(0);
    await expect(marks.nth(TEXT_POSITION)).toHaveAttribute('data-source', 'detected');
  });

  await test.step('a single open page is changed without a selection', async () => {
    await page.getByTestId('strip-view-switch').click();
    await expect(page).toHaveURL(/view=grid/);
    await page.getByRole('button', { name: 'Clear the selection' }).click();
    await page.getByTestId('strip-page').nth(LAST_POSITION).dblclick();
    await expect(page).not.toHaveURL(/view=grid/);
    await expect(menu).toHaveAttribute('data-content', 'text');
    await menu.click();
    await page.getByTestId('content-type-bw-picture').click();
    await expect
      .poll(() => contentOf(page))
      .toEqual([
        'text:detected',
        'color-picture:detected',
        'bw-picture:detected',
        'bw-picture:hand',
      ]);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
