import { expect, test } from '@playwright/test';
import {
  createBook,
  openBookId,
  readPlace,
  registerAndSignIn,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * A book carried whole from one account to another: downloaded as one archive from its About tab, and imported in the
 * library of the other account as a new book of its own, which opens on the stage and the page it was left at.
 */

const PAGES = 6;
const WORDS = 'A book to carry';
// The last segment of the address of a tile of a pyramid
const TILE = 'default.jpg';

test('a downloaded book is imported by another account and opens where it was left', async ({
  page,
  browser,
}) => {
  const folder = await writePagesFolder(PAGES);
  let bookId = '';
  let archive = '';
  let title = '';

  await test.step('a book is left on its third page at the stage Geometry', async () => {
    await registerAndSignIn(page);
    title = await createBook(page, WORDS);
    await uploadFolder(page, folder, PAGES);
    bookId = openBookId(page);
    await page.goto(`/books/${bookId}/stages/geometry`);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await page.getByTestId('strip-page').nth(2).click();
    await expect(page.getByTestId('canvas-caption')).toContainText('3 of 6');
    await expect
      .poll(async () => (await readPlace(page, bookId)).place)
      .toMatchObject({ mode: 'workspace', stage: 'geometry' });
  });

  await test.step('the book is downloaded from its About tab as one archive named after it', async () => {
    await page.goto(`/books/${bookId}/about`);
    const link = page.getByRole('link', { name: 'Download the book' });
    await expect(link).toBeVisible();
    await snap(page, 'book-archive-download');
    const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
    expect(download.suggestedFilename()).toBe(`${title}.zip`);
    archive = test.info().outputPath('book.zip');
    await download.saveAs(archive);
  });

  await test.step('another account imports the archive as a new book, which opens where it was left', async () => {
    const other = await browser.newContext();
    const reader = await other.newPage();
    await registerAndSignIn(reader);
    await expect(reader.getByRole('heading', { name: 'Your books' })).toBeVisible();
    await expect(reader.getByRole('button', { name: 'Import a book' })).toBeVisible();
    await snap(reader, 'book-archive-import');
    // Every picture of the copy is asked for at the address of the copy, which the pyramids of the archive never name
    const failed: string[] = [];
    reader.on('response', (response) => {
      if (response.url().includes('/iiif/') && !response.ok()) {
        failed.push(response.url());
      }
    });
    const tile = reader.waitForResponse(
      (response) =>
        response.url().includes('/iiif/') && response.url().endsWith(TILE) && response.ok(),
    );
    await reader.getByLabel('Archive of a book').setInputFiles(archive);

    await expect(reader).toHaveURL(/\/stages\/geometry/);
    const copyId = openBookId(reader);
    expect(copyId).not.toBe(bookId);
    await expect(reader.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(reader.getByTestId('canvas-caption')).toContainText('3 of 6');
    await expect(reader.getByTestId('strip-page').nth(2).locator('img')).toBeVisible();
    await tile;
    expect(failed).toEqual([]);
    await snap(reader, 'book-archive-imported');

    await reader.getByRole('link', { name: 'Library', exact: true }).click();
    await expect(reader.getByTestId('book-card')).toHaveCount(1);
    await expect(reader.getByTestId('book-card')).toContainText(title);
    await other.close();
  });
});
