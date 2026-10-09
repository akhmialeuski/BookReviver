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
import { changeHeaders, pageIds, runPages } from './support/page-work';

/**
 * The canvas of a page whose result has no tile pyramid yet: the picture cannot be read, so the canvas says it failed
 * instead of loading for ever, and it draws the picture by itself once the rows of the stage say the tiles are cut.
 *
 * The server writes the pyramid of the last step inside the run, so the scenario makes the state it cannot make on
 * demand with the network. The rows of the stages are read with `tiles_ready` off, which makes the canvas ask for the
 * plain preview of the result, and that preview answers 404. The rows come back as the server gives them once the end of a
 * job tells the screen to read them again, and the preview is let through.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const PAGES = 1;

/** Tell the rows of the pages of a stage, which carry the versions the canvas draws, from every other request. */
function isStageRows(url: URL): boolean {
  return /\/stages\/[^/]+\/pages$/.test(url.pathname);
}

/** Tell the plain preview of a version, which the canvas draws while the pyramid of the version is not cut. */
function isPreview(url: URL): boolean {
  return url.pathname.endsWith('/preview.jpg');
}

/** Say of every version in a response that its tile pyramid is not cut, as a version is just after it is made. */
function hideTiles(value: unknown): void {
  if (Array.isArray(value)) {
    for (const item of value) {
      hideTiles(item);
    }
  } else if (typeof value === 'object' && value !== null) {
    const record = value as Record<string, unknown>;
    if (record.tiles_ready === true) {
      record.tiles_ready = false;
    }
    for (const child of Object.values(record)) {
      hideTiles(child);
    }
  }
}

/**
 * Detect again what a page shows and wait until the job has ended. The end of any job makes the screen read the rows of
 * the stages again, which is the one thing the scenario needs from it.
 */
async function readRowsAgain(page: Page): Promise<void> {
  const projectId = openProjectId(page);
  const [first = ''] = await pageIds(page);
  const response = await page.request.post(
    `/api/v1/projects/${projectId}/pages/content-types/detect`,
    { headers: await changeHeaders(page), data: { page_ids: [first] } },
  );
  expect(response.ok()).toBe(true);
  await waitForIdleJobs(page, projectId);
}

test('a page whose result has no pyramid says the picture failed, and draws it once the tiles are cut, on the same page', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const failure = page.getByText('The image of this page could not be loaded');
  let tilesCut = true;
  let here = '';

  await test.step('the stage has run, and its swipe on Margins draws the result from its tile pyramid', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book without tiles for a moment');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await runPages(page);
    // The picture after is what the open step made, so the last step has the result of the stage on the canvas
    await page.getByTestId('bar-step').filter({ hasText: 'Margins' }).click();
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-swipe').click();
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(canvas).toHaveAttribute('data-sources', /info\.json/);
    here = page.url();
  });

  await test.step('the rows say the tiles are not cut and the preview is not there, so the canvas fails with its words', async () => {
    tilesCut = false;
    await page.route(isStageRows, async (route) => {
      const response = await route.fetch();
      const body: unknown = await response.json();
      if (!tilesCut) {
        hideTiles(body);
      }
      await route.fulfill({ response, json: body });
    });
    await page.route(isPreview, (route) =>
      tilesCut ? route.continue() : route.fulfill({ status: 404 }),
    );
    await readRowsAgain(page);
    await expect(canvas).toHaveAttribute('data-state', 'failed');
    await expect(failure).toBeVisible();
    await snap(page, 'compare-picture-failed');
  });

  await test.step('the tiles are cut, the rows are read again, and the canvas draws the pyramid on the same page', async () => {
    tilesCut = true;
    await readRowsAgain(page);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(canvas).toHaveAttribute('data-sources', /info\.json/);
    await expect(failure).toHaveCount(0);
    await expect(page).toHaveURL(here);
    await snap(page, 'compare-picture-after-the-tiles-are-cut');
  });

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await rm(path.dirname(folder), { recursive: true, force: true });
});
