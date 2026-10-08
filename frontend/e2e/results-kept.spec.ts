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
import { openTimeline, RESULT_ROWS, runPages } from './support/page-work';

/**
 * The results a page keeps after their pictures are collected: the collection that a run queues when it ends removes
 * the files of the results that are no longer current and leaves their rows, the list of results says the picture was
 * removed, and using such a result makes the picture again under the same identifier and makes the result the current
 * one.
 */

const PAGES = 1;
const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const PAGE_SIZE = 100;

/** The versions of a stage of the first page of the open book, as the API lists them. */
interface ListedVersion {
  id: string;
  files_removed: boolean;
  images: { full: string } | null;
}

test('a result whose picture was collected is made again when it is used', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const entries = page.locator(RESULT_ROWS);
  let projectId = '';
  let bookPath = '';
  let removedId = '';

  /** List the results of the geometry stage on the first page of the book. */
  async function listVersions(): Promise<ListedVersion[]> {
    const pages = await page.request.get(`/api/v1/projects/${projectId}/pages?size=1`);
    const first = ((await pages.json()) as { items: { id: string }[] }).items[0];
    const listed = await page.request.get(
      `/api/v1/projects/${projectId}/pages/${first?.id}/versions?stage=geometry&size=${PAGE_SIZE}`,
    );
    return ((await listed.json()) as { items: ListedVersion[] }).items;
  }

  await test.step('a page is run, its recipe is changed, and it is run again, so it keeps two results', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book that keeps its results');
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
    await expect(entries).toHaveCount(1);

    await page.getByTestId('bar-step').filter({ hasText: 'Margins' }).click();
    await page
      .getByTestId('step-panel-settings')
      .getByRole('spinbutton', { name: 'Top margin, mm', exact: true })
      .fill('12');
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    // The page is marked to check, which the stale banner does not offer to run, so the stage is run on all pages
    await runPages(page);
    await expect(entries).toHaveCount(2, { timeout: RUN_TIMEOUT_MS });
    await expect(entries.first()).toHaveAttribute('data-current', 'true');
    await waitForIdleJobs(page, projectId);
  });

  await test.step('the run that replaced a result removed its picture and kept its row', async () => {
    const removed = (await listVersions()).filter((version) => version.files_removed);
    expect(removed).toHaveLength(1);
    expect(removed[0]?.images).toBeNull();
    removedId = removed[0]?.id ?? '';
    await page.reload();
    await page.getByTestId('strip-page').first().click();
    await openTimeline(page);
    await expect(entries).toHaveCount(2);
    await expect(entries.nth(1).getByTestId('page-history-removed')).toHaveText(
      'Picture removed · made again on use',
    );
    await expect(entries.first().getByTestId('page-history-removed')).toHaveCount(0);
    await snap(page, 'results-picture-removed');
  });

  await test.step('using the result makes its picture again, and it becomes the current one', async () => {
    await entries.nth(1).getByTestId('page-history-use').click();
    await expect(entries.nth(1)).toHaveAttribute('data-current', 'true', {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(entries.first()).toHaveAttribute('data-current', 'false');
    await expect(entries.nth(1).getByTestId('page-history-removed')).toHaveCount(0);
    await expect(entries.nth(1)).toContainText('Top margin, mm 10');

    // The run queues a collection after it, and the result it replaced loses its picture the same way, so only the one
    // that was used is sure to have its picture
    const versions = await listVersions();
    // The same version, not another one, has its picture again
    const remade = versions.find((version) => version.id === removedId);
    expect(remade?.files_removed).toBe(false);
    expect(remade?.images).not.toBeNull();
    const picture = await page.request.get(remade?.images?.full ?? '');
    expect(picture.ok()).toBe(true);
    await snap(page, 'results-picture-made-again');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
