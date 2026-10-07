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
  writePagesFolder,
} from './support/account';

/**
 * A stage run step by step: the Geometry recipe is run through its first step on every page from the panel of that step and
 * checked, then through its last step, and the first step is found in the cache of versions and not computed again. The
 * panel of the open step says how many pages passed it.
 */

const PAGES = 4;
const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const FIRST = 0;

// Tall enough for the pictures of the key states to show the steps of the recipe and the section of the open page
test.use({ viewport: { width: 1280, height: 1000 } });

interface StoredVersion {
  id: string;
  created_at: string;
}

/** Count the runs of a stage that ended well in the open book, which tells that a run the reader started is over. */
async function finishedRuns(page: Page): Promise<number> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=50`);
  const items = ((await listed.json()) as { items: { kind: string; state: string }[] }).items;
  return items.filter((job) => job.kind === 'run-stage' && job.state === 'succeeded').length;
}

/** Read the full results the Geometry stage made on the first page of the open book, the oldest first. */
async function geometryVersions(page: Page): Promise<StoredVersion[]> {
  const projectId = openProjectId(page);
  const pages = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
  const first = ((await pages.json()) as { items: { id: string; position: number }[] }).items.find(
    (item) => item.position === FIRST,
  );
  if (first === undefined) {
    throw new Error('The book has no first page.');
  }
  const listed = await page.request.get(
    `/api/v1/projects/${projectId}/pages/${first.id}/versions?stage=geometry&scale=full&size=100`,
  );
  const items = ((await listed.json()) as { items: StoredVersion[] }).items;
  return items.toSorted((a, b) => a.created_at.localeCompare(b.created_at));
}

test('the Geometry recipe is run through its first step on all pages, checked, and then through the last step without computing the first again', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const steps = page.getByTestId('bar-step');
  const passed = page.getByTestId('step-passed');
  let stepCount = 0;
  let firstStepVersion: StoredVersion | undefined;

  /** Open the step at a place of the bar, unless it is open already. */
  const openStep = async (index: number): Promise<void> => {
    if ((await steps.nth(index).getAttribute('data-open')) !== 'true') {
      await steps.nth(index).click();
    }
    await expect(steps.nth(index)).toHaveAttribute('data-open', 'true');
  };

  /** Run the recipe up to a step on all pages from the panel of the step, and wait for the run to end. */
  const runThrough = async (index: number): Promise<void> => {
    // A run is refused while the book still splits, collects old versions or does anything else, and a job of the
    // earlier stage that ends now would count as the run started here
    await waitForIdleJobs(page, openProjectId(page));
    await openStep(index);
    const before = await finishedRuns(page);
    await page.getByTestId('step-auto').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
  };

  await test.step('a book opens on the Geometry stage, whose recipe has several steps', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book run step by step');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    // The bar draws the steps once the recipe of the stage is read, so they are counted after the first one shows
    await expect(steps.first()).toBeVisible();
    stepCount = await steps.count();
    expect(stepCount).toBeGreaterThan(1);
    // Nothing has run, so no step has been passed and no page stopped short
    await openStep(FIRST);
    await expect(passed).toHaveText(`0 of ${PAGES} pages passed`);
    await expect(page.getByTestId('strip-stopped-filter')).toHaveCount(0);
  });

  await test.step('"Auto on all pages" in the panel of the first step runs it on every page and leaves the rest', async () => {
    await runThrough(FIRST);
    await expect(passed).toHaveText(`${PAGES} of ${PAGES} pages passed`, {
      timeout: RUN_TIMEOUT_MS,
    });
    await openStep(stepCount - 1);
    await expect(passed).toHaveText(`0 of ${PAGES} pages passed`);
    await expect(page.getByTestId('run-stopped')).toHaveText(
      `Done through step 1 of ${stepCount}: ${PAGES} pages`,
    );
    // The stage is not done while its pages wait for the rest of the steps
    await expect(page.getByTestId('stage-stopped').first()).toBeVisible();
    const made = await geometryVersions(page);
    expect(made).toHaveLength(1);
    firstStepVersion = made[0];
    await snap(page, 'run-through-the-first-step');
  });

  await test.step('the pages are checked by the step they stopped at', async () => {
    const filter = page.getByTestId('strip-stopped-filter');
    await expect(filter).toBeVisible();
    await filter.selectOption({ label: `Stopped at step 1 · ${PAGES}` });
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await page.getByTestId('strip-page').first().click();
    await expect(page.getByTestId('this-page-stopped')).toContainText(
      `Run through step 1 of ${stepCount} only`,
    );
    await page.getByTestId('this-page').scrollIntoViewIfNeeded();
    await snap(page, 'page-stopped-at-the-first-step');
  });

  await test.step('"Auto on all pages" in the panel of the last step finds the first in the cache and makes the rest', async () => {
    await runThrough(stepCount - 1);
    await expect(passed).toHaveText(`${PAGES} of ${PAGES} pages passed`, {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(page.getByTestId('run-stopped')).toHaveCount(0);
    await expect(page.getByTestId('strip-stopped-filter')).toHaveCount(0);
    await expect(page.getByTestId('stage-stopped')).toHaveCount(0);
    const made = await geometryVersions(page);
    expect(made).toHaveLength(stepCount);
    // The version of the first step is the very one the first run made, so that step was not computed again
    expect(made.find((version) => version.id === firstStepVersion?.id)).toEqual(firstStepVersion);
    await page.getByTestId('strip-page').first().click();
    await expect(page.getByTestId('this-page-stopped')).toHaveCount(0);
    await page.getByTestId('this-page').scrollIntoViewIfNeeded();
    await snap(page, 'run-through-the-last-step');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
