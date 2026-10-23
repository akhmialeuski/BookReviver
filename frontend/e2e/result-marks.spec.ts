import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect } from '@playwright/test';
import {
  createBook,
  openLibrary,
  openProjectId,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';
import { test } from './support/fixtures';
import { openTimeline, RESULT_ROWS, runPages } from './support/page-work';

/**
 * The notes a user puts on a result: a good or bad mark and a comment of several lines, which can be changed later,
 * and stay on the result after the page is read again.
 */

const PAGES = 1;
const SCENARIO_TIMEOUT_MS = 180_000;
const RUN_TIMEOUT_MS = 90_000;
const COMMENT = 'Frame too tight on the left.\nTry the other margins.';

test('a result is marked and commented, and the notes can be changed later', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const entry = page.locator(RESULT_ROWS).first();
  let projectId = '';
  let bookPath = '';

  await test.step('a page is run and has one result', async () => {
    await openLibrary(page);
    await createBook(page, 'A book with notes on its results');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    // The import leaves jobs behind it, and a run asked for while they last is refused
    await waitForIdleJobs(page, projectId);
    await runPages(page);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    await page.getByTestId('strip-page').first().click();
    // The results are in the history that ends the panel, which is collapsed until it is opened
    await openTimeline(page);
    await expect(page.locator(RESULT_ROWS)).toHaveCount(1);
  });

  await test.step('the result is marked good and commented', async () => {
    await entry.getByTestId('result-mark-good').click();
    await expect(entry.getByTestId('result-mark-good')).toHaveAttribute('aria-pressed', 'true');
    await entry.getByTestId('result-comment-edit').click();
    await entry.getByRole('textbox', { name: 'Comment' }).fill(COMMENT);
    await entry.getByTestId('result-comment-save').click();
    await expect(entry.getByTestId('result-comment')).toHaveText(COMMENT);
    await snap(page, 'result-marked-good-and-commented');
  });

  await test.step('the mark is changed to bad, and the notes stay after the page is read again', async () => {
    await entry.getByTestId('result-mark-bad').click();
    await expect(entry.getByTestId('result-mark-bad')).toHaveAttribute('aria-pressed', 'true');
    await expect(entry.getByTestId('result-mark-good')).toHaveAttribute('aria-pressed', 'false');
    await page.reload();
    await page.getByTestId('strip-page').first().click();
    await openTimeline(page);
    await expect(entry.getByTestId('result-mark-bad')).toHaveAttribute('aria-pressed', 'true');
    await expect(entry.getByTestId('result-comment')).toHaveText(COMMENT);
    await snap(page, 'result-marked-bad');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
