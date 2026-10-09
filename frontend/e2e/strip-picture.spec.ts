import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writeSheetsFolder,
} from './support/account';
import { runPages } from './support/page-work';
import { versionIdOf } from './support/steps';

/**
 * The strip of pages and the canvas show one picture of a page on every stage and at every step of it: the picture the
 * server gives with the row, which is what the open step reads. The scenario runs Geometry from Margins, so the result of
 * the stage differs from what the earlier steps read, and then walks every step of the stages with a step bar, reading the
 * version of the thumbnail of the open page and the version of the picture the canvas has loaded.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
// How long the rows of a step are held back, in which the strip must not fall back to another picture
const HELD_SAMPLES = 10;
const SAMPLE_MS = 100;
// How many rows one request of the strip asks for, which tells it from the single row the step bar reads for a dot
const ROWS_PAGE_SIZE = '1000';
const STEP_ADDRESS = /\/stages\/[a-z-]+\/steps\/[0-9a-f-]{36}(\?|$)/;

/**
 * The stages with a step bar, the step of each that is shown in the pictures of the run, and the steps whose picture is
 * not the result of the stage, so a strip that drew the result there would be caught.
 */
const BAR_STAGES: readonly { stage: string; picture: string; apart: readonly string[] }[] = [
  { stage: 'geometry', picture: 'Margins', apart: ['Select content', 'Margins'] },
  { stage: 'cleanup', picture: 'Binarization', apart: [] },
];

/** One page version as the rows of a stage list it, with the images the scenario reads. */
type Versioned = { images: { thumbnail: string } | null } | null;

/** The versions of the only page of the book that the rows of a stage give, with and without a step. */
async function rowOf(
  page: Page,
  projectId: string,
  stage: string,
  stepId?: string,
): Promise<{ head: string | null; picture: string | null }> {
  const query = stepId === undefined ? '' : `&step=${stepId}`;
  const listed = await page.request.get(
    `/api/v1/projects/${projectId}/stages/${stage}/pages?size=10${query}`,
  );
  const [item] = ((await listed.json()) as { items: { version: Versioned; picture: Versioned }[] })
    .items;
  return {
    head: versionIdOf(item?.version?.images?.thumbnail),
    picture: versionIdOf(item?.picture?.images?.thumbnail),
  };
}

// Tall enough for the pictures of the key states to show the bar, the canvas and the panel
test.use({ viewport: { width: 1280, height: 1000 } });

test('the strip and the canvas show the same picture of the page at every step of every stage with a bar', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(1);
  const thumbnail = page.getByTestId('strip-page').locator('img');
  const canvas = page.getByTestId('viewer-canvas');
  const barSteps = page.getByTestId('bar-step');
  let projectId = '';
  let bookPath = '';

  // Open the step at a place of the bar, unless it is open already, and give its identifier from the address
  const openStepAt = async (index: number): Promise<string> => {
    const step = barSteps.nth(index);
    const stepId = (await step.getAttribute('data-step-id')) ?? '';
    if ((await step.getAttribute('data-open')) !== 'true') {
      await step.click();
    }
    await expect(step).toHaveAttribute('data-open', 'true');
    await expect(page).toHaveURL(new RegExp(`/steps/${stepId}(\\?|$)`));
    return stepId;
  };

  await test.step('the stage runs from its last step, so Margins makes the result of the stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of strip pictures');
    await uploadFolder(page, folder, 1);
    projectId = openProjectId(page);
    await waitForIdleJobs(page, projectId);
    // A sheet of the fixture is taken for a picture, which the recipe for pictures processes rather than the recipe for text
    await markPagesAsText(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    // A stage that never ran opens on the last step of its recipe, which is Margins, and asks for a preview of the page;
    // the server refuses a run while that job holds the project
    const previewAsked = page.waitForResponse((response) =>
      response.url().endsWith('/stages/geometry/preview'),
    );
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('strip-page')).toHaveCount(1);
    await previewAsked;
    await runPages(page);
  });

  await test.step('a click on Geometry in the stage bar opens a step, not the stage alone', async () => {
    await page.goto(`${bookPath}/stages/cleanup`);
    await expect(page).toHaveURL(STEP_ADDRESS);
    await page.getByTestId('stage-geometry').locator('visible=true').first().click();
    await expect(page).toHaveURL(/\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/);
    await expect(page.getByTestId('panel-step')).toHaveCount(1);
  });

  await test.step('while the rows of a step load the strip shows no picture, not the result of the stage', async () => {
    // A reload empties the cache of the rows, so the step that is opened next is read from the server
    await page.reload();
    await expect(page.getByTestId('strip-page')).toHaveCount(1);
    const { head } = await rowOf(page, projectId, 'geometry');
    expect(head).not.toBeNull();
    const first = await openStepAt(0);
    const { picture } = await rowOf(page, projectId, 'geometry', first);
    // The control: without it the check below could pass with a result of the stage equal to the picture of the step
    expect(picture).not.toBe(head);
    await openStepAt(1);

    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let held = 0;
    await page.route(
      (url) =>
        /\/stages\/[^/]+\/pages$/.test(url.pathname) &&
        url.searchParams.has('step') &&
        url.searchParams.get('size') === ROWS_PAGE_SIZE,
      async (route) => {
        held += 1;
        await gate;
        await route.continue();
      },
    );
    await openStepAt(2);
    await expect.poll(() => held).toBeGreaterThan(0);
    for (let sample = 0; sample < HELD_SAMPLES; sample += 1) {
      // No picture at all: the tile waits with its words, and never draws the result of the stage
      expect(await thumbnail.count()).toBe(0);
      await page.waitForTimeout(SAMPLE_MS);
    }
    release();
    const { picture: third } = await rowOf(
      page,
      projectId,
      'geometry',
      (await barSteps.nth(2).getAttribute('data-step-id')) ?? '',
    );
    await expect
      .poll(async () => versionIdOf(await thumbnail.getAttribute('src')), {
        timeout: RUN_TIMEOUT_MS,
      })
      .toBe(third);
    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  for (const { stage, picture, apart } of BAR_STAGES) {
    await test.step(`on ${stage} every step shows the picture the strip shows on its canvas`, async () => {
      await page.goto(`${bookPath}/stages/${stage}`);
      await expect(page).toHaveURL(STEP_ADDRESS);
      await expect(page.getByTestId('strip-page')).toHaveCount(1);
      const count = await barSteps.count();
      expect(count).toBeGreaterThan(1);
      // The version the stage as a whole ended with, which is what the strip shows when it falls back to the stage
      const { head } = await rowOf(page, projectId, stage);
      for (let index = 0; index < count; index += 1) {
        const stepId = await openStepAt(index);
        const title = (await barSteps.nth(index).textContent()) ?? '';
        await expect(canvas).toHaveAttribute('data-state', 'ready', { timeout: RUN_TIMEOUT_MS });
        // The row of the step says which version is the picture, so the strip is read against the server and the canvas
        const { picture: expected } = await rowOf(page, projectId, stage, stepId);
        expect(expected).not.toBeNull();
        await expect(async () => {
          const shown = versionIdOf(await thumbnail.getAttribute('src'));
          // The first address is the picture before, which is the picture of the page; a compare adds the one after
          const drawn = versionIdOf(
            (await canvas.getAttribute('data-sources'))?.split(' ')[0] ?? null,
          );
          expect(shown).not.toBeNull();
          expect(drawn).toBe(shown);
          expect(shown).toBe(expected);
        }).toPass({ timeout: RUN_TIMEOUT_MS });
        if (apart.some((step) => title.includes(step))) {
          // These steps read a picture that is not the result of the stage, and the strip shows what the step reads
          expect(head).not.toBeNull();
          expect(versionIdOf(await thumbnail.getAttribute('src'))).not.toBe(head);
        }
        if (title.includes(picture)) {
          await snap(page, `${stage}-${picture.toLowerCase()}`);
        }
      }
    });
  }

  await rm(path.dirname(folder), { recursive: true, force: true });
});
