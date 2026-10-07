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
import { openTimeline, RESULT_ROWS } from './support/page-work';

/**
 * The notes a user puts on a result: a good or bad mark and a comment of several lines, which can be changed later,
 * stay on the result after the page is read again, and are logged by the server with each change.
 */

const PAGES = 1;
const SCENARIO_TIMEOUT_MS = 180_000;
const RUN_TIMEOUT_MS = 90_000;
const COMMENT = 'Frame too tight on the left.\nTry the other margins.';
const CHANGES = 3;

/** One change of the log of a result, as the API lists it. */
interface ListedChange {
  sequence: number;
  mark_after: string | null;
  comment_after: string;
}

test('a result is marked and commented, and the notes can be changed later', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const entry = page.locator(RESULT_ROWS).first();
  let projectId = '';
  let bookPath = '';

  await test.step('a page is run and has one result', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with notes on its results');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    // The import leaves jobs behind it, and a run asked for while they last is refused
    await waitForIdleJobs(page, projectId);
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
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

  await test.step('the log of the result holds the three changes in the order they were made', async () => {
    const pages = await page.request.get(`/api/v1/projects/${projectId}/pages?size=1`);
    const first = ((await pages.json()) as { items: { id: string }[] }).items[0];
    // The stage keeps a version for each of its steps, so the marked one is read from the entry itself
    const versionId = await entry.getByTestId('result-mark-good').getAttribute('data-version');
    const listed = await page.request.get(
      `/api/v1/projects/${projectId}/pages/${first?.id}/versions/${versionId}/mark-changes`,
    );
    const log = ((await listed.json()) as { items: ListedChange[] }).items;
    expect(log.map((change) => [change.sequence, change.mark_after, change.comment_after])).toEqual(
      [
        [1, 'good', ''],
        [2, 'good', COMMENT],
        [CHANGES, 'bad', COMMENT],
      ],
    );
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
