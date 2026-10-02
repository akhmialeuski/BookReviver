import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  addPlaceholderPages,
  createBook,
  openImportStage,
  registerAndSignIn,
  snap,
  writePagesFolder,
} from './support/account';

/**
 * A long book and a long job: the strip of a book of a thousand pages keeps only the pages in sight in the document
 * while it scrolls from the first page to the last, and the activity of the book stops an import that is still running.
 *
 * The thousand pages are placeholders added through the pages route, since importing a thousand scans would spend the
 * run on the import rather than on the strip.
 */

const LONG_BOOK_PAGES = 1000;
// Far more than a strip shows at once, and far fewer than the book has
const MOST_TILES_IN_DOCUMENT = 40;
const SCROLL_STEPS = 10;
const LONG_BOOK_TIMEOUT_MS = 180_000;
// Enough files that their import is still running when the reader reaches the Stop button
const STOPPED_IMPORT_FILES = 300;

test('the strip of a book of a thousand pages keeps only the pages in sight', async ({ page }) => {
  test.setTimeout(LONG_BOOK_TIMEOUT_MS);
  const tiles = page.getByTestId('strip-page');
  const scroller = page.getByTestId('strip-scroll');

  await test.step('a book gets a thousand pages', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A long book');
    await addPlaceholderPages(page, LONG_BOOK_PAGES, 'geometry');
  });

  await test.step('the strip of a stage lists the whole book and draws only its start', async () => {
    await expect(page.getByTestId('stage-screen')).toHaveAttribute('data-stage', 'geometry');
    await expect(tiles.first()).toContainText('1');
    expect(await tiles.count()).toBeLessThanOrEqual(MOST_TILES_IN_DOCUMENT);
    await snap(page, 'strip-first-page');
  });

  await test.step('scrolling to the last page keeps the document as small as at the start', async () => {
    for (let step = 1; step <= SCROLL_STEPS; step += 1) {
      await scroller.evaluate((element, fraction) => {
        element.scrollTop = (element.scrollHeight - element.clientHeight) * fraction;
      }, step / SCROLL_STEPS);
      await expect(tiles.last()).toBeVisible();
      expect(await tiles.count()).toBeLessThanOrEqual(MOST_TILES_IN_DOCUMENT);
    }
    await expect(tiles.last()).toContainText(String(LONG_BOOK_PAGES));
    await snap(page, 'strip-last-page');
  });
});

test('the activity of a book stops an import that is still running', async ({ page }) => {
  test.setTimeout(LONG_BOOK_TIMEOUT_MS);
  const folder = await writePagesFolder(STOPPED_IMPORT_FILES);
  const chip = page.getByTestId('activity-chip');
  const importJob = page.getByTestId('activity-job').filter({ hasText: 'Import' }).first();

  try {
    await test.step('a large folder starts an import that the chip shows with its progress', async () => {
      await registerAndSignIn(page);
      await createBook(page, 'A stopped import');
      await openImportStage(page);
      await page.getByTestId('folder-input').setInputFiles(folder);
      await expect(chip).toHaveAttribute('data-busy', 'true', { timeout: LONG_BOOK_TIMEOUT_MS });
      await expect(chip).toContainText('Import');
    });

    await test.step('Stop in the list of jobs cancels the import', async () => {
      await chip.click();
      await importJob.getByRole('button', { name: 'Stop' }).click();
      await expect(importJob).toHaveAttribute('data-state', 'cancelled');
      await expect(chip).toHaveAttribute('data-busy', 'false');
    });

    await test.step('the book keeps fewer files than the folder held', async () => {
      await page.keyboard.press('Escape');
      expect(await page.getByTestId('source-name').count()).toBeLessThan(STOPPED_IMPORT_FILES);
    });
  } finally {
    await rm(path.dirname(folder), { recursive: true, force: true });
  }
});
