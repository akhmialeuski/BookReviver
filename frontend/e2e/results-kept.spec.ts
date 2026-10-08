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
import { changeHeaders, openTimeline, pageIds, RESULT_ROWS, runPages } from './support/page-work';

/**
 * The results a page keeps while its old results are collected: the collection that a run queues when it ends deletes the
 * results the run replaced, with their pictures and rows, unless a result is marked good or commented, and the results
 * that are kept stay in the list of the page. "Clear old results" in the About tab of the book counts what a collection
 * would delete now and the room it frees, and deletes it when it is confirmed.
 *
 * A result that is marked good is old once the mark is taken off, and no run follows that, so it is the one the button
 * finds.
 */

const PAGES = 1;
const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 90_000;
const COMMENT = 'Keep this frame for comparison.';
// The top margin the recipe starts with is 10, and each run below changes it, so each makes a result of its own
const TOP_MARGINS = ['12', '14', '16'];
const GOOD_MARK = 'result-mark-good';

test('a collection deletes the results a run replaced and keeps the good and the commented ones, and the button clears the rest', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const entries = page.locator(RESULT_ROWS);
  let projectId = '';
  let pageId = '';
  let bookPath = '';
  // The results of the page from the oldest, by the identifier of the version each stands for
  const made: string[] = [];

  /** Read the identifiers of the versions the list of results shows, the current one first. */
  const listed = (): Promise<(string | null)[]> =>
    entries.evaluateAll(
      (rows, mark) =>
        rows.map(
          (row) =>
            row.querySelector(`[data-testid="${mark}"]`)?.getAttribute('data-version') ?? null,
        ),
      GOOD_MARK,
    );

  /** Read the status the server answers for a version of the page: 200 while it is there, 404 once it is deleted. */
  const statusOf = async (versionId: string | undefined): Promise<number> =>
    (
      await page.request.get(`/api/v1/projects/${projectId}/pages/${pageId}/versions/${versionId}`)
    ).status();

  /** Change the top margin of the recipe, save it, and run the stage, which makes a new current result. */
  const runWithTopMargin = async (millimetres: string): Promise<void> => {
    await page.getByTestId('bar-step').filter({ hasText: 'Margins' }).click();
    await page
      .getByTestId('step-panel-settings')
      .getByRole('spinbutton', { name: 'Top margin, mm', exact: true })
      .fill(millimetres);
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    await runPages(page);
  };

  /** Read what the About tab says a collection would delete, which is the report the dialog is drawn from. */
  const collectable = async (): Promise<{ versions: number; size_bytes: number }> => {
    const response = await page.request.get(`/api/v1/projects/${projectId}/versions/collectable`);
    return (await response.json()) as { versions: number; size_bytes: number };
  };

  /** Open the dialog of "Clear old results" in the About tab of the book. */
  const openClearing = async (): Promise<void> => {
    await page.goto(`/projects/${projectId}/about`);
    await page.getByRole('button', { name: 'Clear old results' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
  };

  /** Open the results of the page in the Geometry stage, which the history of the page lists. */
  const openResults = async (): Promise<void> => {
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await page.getByTestId('strip-page').first().click();
    await openTimeline(page);
  };

  await test.step('a page is run, so it has one result', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book that keeps its results');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    [pageId = ''] = await pageIds(page);
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
    await expect(entries).toHaveCount(1);
    made.push(...((await listed()) as string[]));
  });

  await test.step('a result marked good stays beside the new one when a run replaces it', async () => {
    await entries.first().getByTestId(GOOD_MARK).click();
    await expect(entries.first().getByTestId(GOOD_MARK)).toHaveAttribute('aria-pressed', 'true');
    await runWithTopMargin(TOP_MARGINS[0] ?? '');
    await expect(entries).toHaveCount(2, { timeout: RUN_TIMEOUT_MS });
    await expect(entries.first()).toHaveAttribute('data-current', 'true');
    const [current] = await listed();
    made.push(current ?? '');
    expect(await statusOf(made[0])).toBe(200);
  });

  await test.step('a result nothing marks is deleted with its row when the next run replaces it', async () => {
    await runWithTopMargin(TOP_MARGINS[1] ?? '');
    await expect(entries).toHaveCount(2, { timeout: RUN_TIMEOUT_MS });
    const [current, earlier] = await listed();
    made.push(current ?? '');
    // The good result is the one beside the new one, and the result between them is gone
    expect(earlier).toBe(made[0]);
    expect(await statusOf(made[1])).toBe(404);
    expect(await statusOf(made[0])).toBe(200);
  });

  await test.step('a commented result stays when the next run replaces it', async () => {
    await entries.first().getByTestId('result-comment-edit').click();
    await entries.first().getByRole('textbox', { name: 'Comment' }).fill(COMMENT);
    await entries.first().getByTestId('result-comment-save').click();
    await expect(entries.first().getByTestId('result-comment')).toHaveText(COMMENT);
    await runWithTopMargin(TOP_MARGINS[2] ?? '');
    await expect(entries).toHaveCount(3, { timeout: RUN_TIMEOUT_MS });
    const [current, commented, good] = await listed();
    expect([commented, good]).toEqual([made[2], made[0]]);
    made.push(current ?? '');
    expect(await statusOf(made[2])).toBe(200);
  });

  await test.step('a result whose good mark is taken off is old, and the button of the About tab counts it with the room it frees', async () => {
    const response = await page.request.put(
      `/api/v1/projects/${projectId}/pages/${pageId}/versions/${made[0]}/mark`,
      { headers: await changeHeaders(page), data: { mark: null, comment: '' } },
    );
    expect(response.ok()).toBe(true);
    const report = await collectable();
    expect(report.versions).toBeGreaterThanOrEqual(1);
    expect(report.size_bytes).toBeGreaterThan(0);
    await openClearing();
    const noun = report.versions === 1 ? 'result' : 'results';
    await expect(page.getByRole('dialog')).toContainText(`${report.versions} old ${noun}`);
    await expect(page.getByRole('dialog')).toContainText('which frees');
    await snap(page, 'clear-old-results');
  });

  await test.step('the confirmed button deletes the old result, and the list of the page keeps the other results', async () => {
    await page.getByTestId('clear-old-results-submit').click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await waitForIdleJobs(page, projectId);
    expect(await statusOf(made[0])).toBe(404);
    expect(await collectable()).toMatchObject({ versions: 0 });
    await openResults();
    await expect(entries).toHaveCount(2);
    expect(await listed()).toEqual([made[3], made[2]]);
  });

  await test.step('the dialog then says there is nothing to clear, and offers no deletion', async () => {
    await openClearing();
    await expect(page.getByRole('dialog')).toContainText('The book has no old results to clear.');
    await expect(page.getByTestId('clear-old-results-submit')).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
