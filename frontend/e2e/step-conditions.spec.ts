import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  deleteAsReader,
  openProjectId,
  registerAndSignIn,
  setKind,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';

/**
 * The same processor twice in one recipe: a second Deskew for the pictures of the book beside the first, which now
 * processes the text pages only. A run through the last step makes each page pass the Deskew that is not for it, so a text
 * page is skipped by the second one and a plate by the first.
 */

const PAGES = 3;
const PLATE_POSITION = 1;
const TEXT_POSITION = 0;
const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const DESKEW = 'geometry.deskew';
const FIRST_DESKEW_INDEX = 1;

// Tall enough for the pictures of the key states to show the steps of the recipe with the open one
test.use({ viewport: { width: 1280, height: 1000 } });

interface ListedVersion {
  processor: { key: string };
  created_at: string;
  data: Record<string, unknown>;
}

/** Read the identifier of the page at a position of the open book. */
async function pageAt(page: Page, position: number): Promise<string> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  const found = items.find((item) => item.position === position);
  if (found === undefined) {
    throw new Error(`The book has no page at position ${position}.`);
  }
  return found.id;
}

/** Read the full versions the Geometry stage made on a page with the Deskew processor, the one of the earlier step first. */
async function deskewVersions(page: Page, pageId: string): Promise<ListedVersion[]> {
  const listed = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/versions?stage=geometry&scale=full&size=100`,
  );
  const items = ((await listed.json()) as { items: ListedVersion[] }).items;
  return items
    .filter((version) => version.processor.key === DESKEW)
    .toSorted((a, b) => a.created_at.localeCompare(b.created_at));
}

/** Count the runs of a stage that ended well in the open book, which tells that a run the reader started is over. */
async function finishedRuns(page: Page): Promise<number> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=50`);
  const items = ((await listed.json()) as { items: { kind: string; state: string }[] }).items;
  return items.filter((job) => job.kind === 'run-stage' && job.state === 'succeeded').length;
}

/** Take away the rules of the Geometry stage, so that every page of the book is made by the one recipe that is shown. */
async function removeRules(page: Page): Promise<void> {
  const base = `/api/v1/projects/${openProjectId(page)}/stages/geometry/rules`;
  const listed = await page.request.get(base);
  const rules = ((await listed.json()) as { items: { id: string }[] }).items;
  for (const rule of rules) {
    expect(await deleteAsReader(page, `${base}/${rule.id}`)).toBeLessThan(400);
  }
}

test('a second Deskew for the pictures skips the text pages, and the first Deskew, kept for the text, skips the plate', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const steps = page.getByTestId('recipe-step');
  let stepCount = 0;

  await test.step('a book of three pages with a plate opens on the Geometry stage, and every page is made by the one recipe', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with two deskews');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await setKind(page, PLATE_POSITION, 'plate');
    await removeRules(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(steps.first()).toBeVisible();
    stepCount = await steps.count();
  });

  await test.step('a second Deskew is added for the pictures, and the first is kept for the text pages', async () => {
    await page.getByTestId('step-add').click();
    await page.getByRole('menuitem', { name: 'Deskew', exact: true }).click();
    await expect(steps).toHaveCount(stepCount + 1);
    const added = steps.nth(stepCount);
    await expect(added.getByTestId('step-condition')).toHaveValue('all');
    await added.getByTestId('step-condition').selectOption('pictures');
    await snap(page, 'second-deskew-for-pictures');

    const first = steps.nth(FIRST_DESKEW_INDEX);
    await expect(first).toHaveAttribute('data-processor', DESKEW);
    await first.getByTestId('step-toggle').click();
    await first.getByTestId('step-condition').selectOption('text');
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    // The server gave the added step an identifier of its own, which its edits are kept under
    const ids = await stepIdsOf(page, 'geometry', DESKEW);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
  });

  await test.step('the recipe is run through its last step on every page, which is the step with the second Deskew', async () => {
    await waitForIdleJobs(page, openProjectId(page));
    const before = await finishedRuns(page);
    await steps.nth(stepCount).getByTestId('step-run').click();
    await page.getByTestId('step-run-all').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
    await expect(steps.nth(stepCount).getByTestId('step-passed')).toHaveText(
      `${PAGES} of ${PAGES} pages passed`,
      { timeout: RUN_TIMEOUT_MS },
    );
  });

  await test.step('the text page was skipped by the second Deskew and the plate by the first', async () => {
    const [textFirst, textSecond] = await deskewVersions(page, await pageAt(page, TEXT_POSITION));
    const [plateFirst, plateSecond] = await deskewVersions(
      page,
      await pageAt(page, PLATE_POSITION),
    );
    expect(textFirst?.data.skipped_by_condition).toBeUndefined();
    expect(textSecond?.data.skipped_by_condition).toBe(true);
    expect(plateFirst?.data.skipped_by_condition).toBe(true);
    expect(plateSecond?.data.skipped_by_condition).toBeUndefined();
    await page.getByTestId('strip-page').nth(PLATE_POSITION).click();
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await snap(page, 'plate-made-by-the-second-deskew');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
