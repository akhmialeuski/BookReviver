import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../src/shared/http/csrf';
import { MESSAGES } from '../src/shared/messages';
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
 * A variant of a stage for a group of pages: a rule that sends the plates of the book to a variant of the Geometry recipe,
 * a run on all pages that gives each page its own variant, a variant pinned to one page that the run keeps, and the
 * strip that marks and filters the pages by variant.
 */

const PAGES = 4;
const PLATE_POSITION = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const DESKEW_PROCESSOR = 'geometry.deskew';

// Tall enough for the pictures of the key states to show two pages of the strip and a section of the panel
test.use({ viewport: { width: 1280, height: 1000 } });

/** Change the kind of the page at a position of the open book, as the Order stage does, straight through the API. */
async function setKind(page: Page, position: number, kind: string): Promise<void> {
  const projectId = openProjectId(page);
  const listed = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  const target = items.find((item) => item.position === position);
  if (target === undefined) {
    throw new Error(`The book has no page at position ${position}.`);
  }
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  const response = await page.request.patch(`/api/v1/projects/${projectId}/pages/${target.id}`, {
    headers: { [CSRF_HEADER_NAME]: token },
    data: { kind },
  });
  expect(response.ok()).toBe(true);
}

/** Read the name of the active recipe of a stage of the open book, which the API lists first. */
async function activeRecipeName(page: Page, stage: string): Promise<string> {
  const listed = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/stages/${stage}/variants?size=100`,
  );
  const items = ((await listed.json()) as { items: { name: string; active: boolean }[] }).items;
  const active = items.find((item) => item.active);
  if (active === undefined) {
    throw new Error(`The stage ${stage} has no active recipe.`);
  }
  return active.name;
}

/** Count the runs of a stage that ended well in the open book, which tells that a run the reader started is over. */
async function finishedRuns(page: Page): Promise<number> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=20`);
  const items = ((await listed.json()) as { items: { kind: string; state: string }[] }).items;
  return items.filter((job) => job.kind === 'run-stage' && job.state === 'succeeded').length;
}

test('plates get their own variant by a rule, a pinned variant survives a run on all pages, and the strip marks them', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const strip = page.getByTestId('strip-page');
  const marks = page.getByTestId('strip-variant');
  const runAll = async (): Promise<void> => {
    const before = await finishedRuns(page);
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    // The summary reads "up to date" before a run of pages that are up to date has begun, so the job itself is awaited
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    // The collection of old versions that follows the run refuses what the next step asks while it is queued or going
    await waitForIdleJobs(page, openProjectId(page));
  };
  let bookPath = '';
  // The names of the two variants, read from the book once it is open: the active recipe, and the copy made of it
  let textVariant = '';
  let platesVariant = '';

  await test.step('a book with a plate among its pages opens on the Geometry stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with plates');
    await uploadFolder(page, folder, PAGES);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await setKind(page, PLATE_POSITION, 'plate');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(strip).toHaveCount(PAGES);
    // One recipe has nothing to tell the pages apart by, so the strip marks none
    await expect(marks).toHaveCount(0);
    await expect(page.getByTestId('strip-variant-filter')).toHaveCount(0);
    textVariant = await activeRecipeName(page, 'geometry');
    platesVariant = MESSAGES.processing.recipe.copyName(textVariant);
  });

  await test.step('a copy of the recipe with settings of its own is the variant for the plates', async () => {
    await page.getByTestId('recipe-new').click();
    // The list holds the copy as an option before the screen switches to it, so the chosen option is awaited
    await expect(page.getByTestId('recipe-select').locator('option:checked')).toContainText(
      platesVariant,
    );
    await expect(page.getByTestId('recipe-active')).toHaveCount(0);
    // The recipe has several steps, closed at first, and only the deskew one has a largest slant
    const deskew = page.locator(
      `[data-testid="recipe-step"][data-processor="${DESKEW_PROCESSOR}"]`,
    );
    await deskew.getByTestId('step-toggle').click();
    await deskew.getByRole('spinbutton', { name: 'Largest slant' }).fill('9');
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
  });

  await test.step('a rule sends the plates and frontispieces to the variant, which the section lists', async () => {
    await expect(page.getByTestId('used-for-empty')).toBeVisible();
    await page.getByTestId('rule-add').click();
    await page.getByTestId('rule-add-plates').click();
    await expect(page.getByTestId('rule')).toHaveCount(1);
    await expect(page.getByTestId('rule')).toContainText('Plates and frontispieces');
    await page.getByTestId('used-for').scrollIntoViewIfNeeded();
    await snap(page, 'used-for-section');
  });

  await test.step('a run on all pages by the active recipe gives the plate the variant and the text pages the active recipe', async () => {
    // The first option is the active recipe, which a run names no recipe for, so the rules choose
    await page.getByTestId('recipe-select').selectOption({ index: 0 });
    await expect(page.getByTestId('recipe-active')).toBeVisible();
    await runAll();
    await expect(marks).toHaveCount(PAGES, { timeout: RUN_TIMEOUT_MS });
    const plateMarks = page.locator(
      `[data-testid="strip-variant"][data-variant="${platesVariant}"]`,
    );
    await expect(plateMarks).toHaveCount(1);
    await expect(
      page.locator(`[data-testid="strip-variant"][data-variant="${textVariant}"]`),
    ).toHaveCount(PAGES - 1);
    await expect(page.getByTestId('variant-counts')).toContainText(`${textVariant} ${PAGES - 1}`);
    await expect(page.getByTestId('variant-counts')).toContainText(`${platesVariant} 1`);
    await snap(page, 'strip-variant-marks');
  });

  await test.step('the strip is narrowed to the pages of one variant, and back to all', async () => {
    const filter = page.getByTestId('strip-variant-filter');
    await filter.selectOption({ label: `${platesVariant} · 1` });
    await expect(strip).toHaveCount(1);
    await expect(marks).toHaveAttribute('data-variant', platesVariant);
    await strip.first().click();
    await expect(page.getByTestId('page-variant')).toContainText(platesVariant);
    await expect(page.getByTestId('page-variant-source')).toHaveText('By the rules of the book');
    await page.getByTestId('apply-to').scrollIntoViewIfNeeded();
    await snap(page, 'plate-by-its-own-variant');
    await filter.selectOption({ label: 'All variants' });
    await expect(strip).toHaveCount(PAGES);
  });

  await test.step('a variant applied to a text page pins it, and a run on all pages keeps the pin', async () => {
    // The recipes are listed with the active one first, and the copy after it
    await page.getByTestId('recipe-select').selectOption({ index: 1 });
    await strip.first().click();
    await page.getByTestId('apply-menu').click();
    await page.getByTestId('apply-page').click();
    const first = page.locator(`[data-testid="strip-page"] [data-testid="strip-variant"]`).first();
    await expect(first).toHaveAttribute('data-variant', platesVariant, {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(first).toHaveAttribute('data-pinned', 'true');
    await expect(page.getByTestId('page-variant-source')).toHaveText('Pinned to this page');

    // The run that pinned the variant is over, with the collection that follows it, once the book has no job to wait for
    await waitForIdleJobs(page, openProjectId(page));
    await expect(page.getByTestId('run-menu')).toBeEnabled({ timeout: RUN_TIMEOUT_MS });
    await page.getByTestId('recipe-select').selectOption({ index: 0 });
    await runAll();
    await expect(first).toHaveAttribute('data-variant', platesVariant);
    await expect(first).toHaveAttribute('data-pinned', 'true');
  });

  await test.step('"Use the book\'s rules" takes the pin off, and the next run gives the page the active recipe', async () => {
    await page.getByTestId('use-rules').click();
    await expect(page.getByTestId('use-rules')).toHaveCount(0, { timeout: RUN_TIMEOUT_MS });
    await runAll();
    const first = page.locator(`[data-testid="strip-page"] [data-testid="strip-variant"]`).first();
    await expect(first).toHaveAttribute('data-variant', textVariant, { timeout: RUN_TIMEOUT_MS });
    await expect(first).toHaveAttribute('data-pinned', 'false');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
