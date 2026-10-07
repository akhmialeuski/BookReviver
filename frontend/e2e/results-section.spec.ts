import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';
import { openTimeline, RESULT_ROWS, runAllPages } from './support/page-work';

/**
 * The results of a step on a page: the open step lists them in the history that ends the panel of the stage, a result
 * marked bad puts a mark on the thumbnail of its page and an entry in the strip filter, and the history narrows its list
 * by the mark.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 180_000;
const RUN_TIMEOUT_MS = 90_000;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;

// Tall enough for the pictures of the key states to show the strip, the canvas and the section of the step
test.use({ viewport: { width: 1280, height: 1000 } });

test('a result of a step is marked bad, which marks its page in the strip, and the section is narrowed by the mark', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const strip = page.getByTestId('page-strip');
  const section = page.getByTestId('stage-panel').getByTestId('page-history');
  const entry = section.locator(RESULT_ROWS);

  await test.step('the recipe is run on every page and its first step is opened', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with results to mark');
    await uploadFolder(page, folder, PAGES);
    const projectId = openProjectId(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(strip.getByTestId('strip-page')).toHaveCount(PAGES);
    // The import leaves jobs behind it, and a run asked for while they last is refused
    await waitForIdleJobs(page, projectId);
    await runAllPages(page);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    await page.getByTestId('bar-step').first().click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await openTimeline(page);
    await expect(entry).toHaveCount(1);
    await expect(entry.getByTestId('page-history-origin')).toHaveText('Made by the step');
    // The history lists the results once, and neither the panel of the step nor the section of the page does
    await expect(page.getByTestId('page-history')).toHaveCount(1);
    await expect(page.getByTestId('step-panel').locator(RESULT_ROWS)).toHaveCount(0);
    await expect(page.getByTestId('this-page').locator(RESULT_ROWS)).toHaveCount(0);
  });

  await test.step('no page is marked, and the strip filter offers the pages marked bad', async () => {
    await expect(strip.getByTestId('strip-marked-bad')).toHaveCount(0);
    await expect(strip.getByTestId('strip-filter-bad')).toHaveText('Marked bad 0');
  });

  await test.step('the result is marked bad, and its page is marked on the thumbnail and in the filter', async () => {
    await entry.getByTestId('result-mark-bad').click();
    await expect(entry.getByTestId('result-mark-bad')).toHaveAttribute('aria-pressed', 'true');
    await expect(strip.getByTestId('strip-marked-bad')).toHaveCount(1);
    await expect(strip.getByTestId('strip-filter-bad')).toHaveText('Marked bad 1');
    await snap(page, 'result-marked-bad-in-the-strip');
  });

  await test.step('the strip lists the page marked bad only when the filter is on', async () => {
    await strip.getByTestId('strip-filter-bad').click();
    await expect(strip.getByTestId('strip-page')).toHaveCount(1);
    await expect(strip.getByTestId('strip-page').getByTestId('strip-marked-bad')).toHaveCount(1);
    await strip.getByTestId('strip-filter-all').click();
    await expect(strip.getByTestId('strip-page')).toHaveCount(PAGES);
  });

  await test.step('the history lists the result under Bad and nothing under Good', async () => {
    await section.getByTestId('page-history-filter-good').click();
    await expect(entry).toHaveCount(0);
    await expect(section.getByTestId('page-history-empty')).toBeVisible();
    await section.getByTestId('page-history-filter-bad').click();
    await expect(entry).toHaveCount(1);
    await section.getByTestId('page-history-filter-all').click();
    await expect(entry).toHaveCount(1);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
