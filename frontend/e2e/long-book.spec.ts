import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect } from '@playwright/test';
import {
  addPlaceholderPages,
  createBook,
  openImportStage,
  openLibrary,
  openProjectId,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';
import { test } from './support/fixtures';

/**
 * A long book and a long job: the strip of a book of a thousand pages keeps only the pages in sight in the document
 * while it scrolls from the first page to the last, the activity of the book stops an import that is still running, and
 * a reload of the page in the middle of a run finds the run going on, since the server holds the jobs of the book.
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
// Enough pages that a run of Geometry over the book is still going when the page has been reloaded, and few enough that
// the run ends within the scenario while the other workers load the server
const RELOADED_RUN_PAGES = 12;

test('the strip of a book of a thousand pages keeps only the pages in sight', async ({ page }) => {
  test.setTimeout(LONG_BOOK_TIMEOUT_MS);
  const tiles = page.getByTestId('strip-page');
  const scroller = page.getByTestId('strip-scroll');

  await test.step('a book gets a thousand pages', async () => {
    await openLibrary(page);
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
      await openLibrary(page);
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

test('a reload of the page in the middle of a run shows the same run, which goes on to its end', async ({
  page,
}) => {
  test.setTimeout(LONG_BOOK_TIMEOUT_MS);
  const folder = await writePagesFolder(RELOADED_RUN_PAGES);
  const chip = page.getByTestId('activity-chip');
  const runJob = page.getByTestId('activity-job').filter({ hasText: 'Geometry' }).first();

  try {
    await test.step('a run of Geometry over the whole book starts, and the chip shows it', async () => {
      await openLibrary(page);
      await createBook(page, 'A reloaded run');
      await uploadFolder(page, folder, RELOADED_RUN_PAGES);
      const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
      await page.goto(`${bookPath}/stages/geometry`);
      // The strip keeps only the pages in sight, so the stage is told open by its title and not by its pages
      await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
      // The editor of the step opens with a preview of its own, which is over before the run is asked for
      await waitForIdleJobs(page, openProjectId(page));
      await page.getByTestId('run-menu').click();
      await page.getByTestId('run-pages-all').click();
      await page.keyboard.press('Escape');
      await page.getByTestId('run-start').click();
      await expect(chip).toHaveAttribute('data-busy', 'true');
      await expect(chip).toContainText('Geometry');
    });

    await test.step('after a reload the chip shows the same run still going, as the server holds it', async () => {
      await page.reload();
      await expect(chip).toHaveAttribute('data-busy', 'true');
      await chip.click();
      await expect(runJob).toHaveAttribute('data-state', 'running');
      await snap(page, 'run-after-reload');
      await page.keyboard.press('Escape');
    });

    await test.step('the run goes on to its end, which the reloaded page learns from the stream', async () => {
      await expect(chip).toHaveAttribute('data-busy', 'false', { timeout: LONG_BOOK_TIMEOUT_MS });
      await chip.click();
      await expect(runJob).toHaveAttribute('data-state', 'succeeded');
    });
  } finally {
    await rm(path.dirname(folder), { recursive: true, force: true });
  }
});
