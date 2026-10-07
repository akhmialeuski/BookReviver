import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  setKind,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import { runAllPages } from './support/page-work';

/**
 * A variant of a stage for a group of pages: the rule a new book starts with, which sends its plates to the variant
 * Plates of the Geometry recipe, a run on all pages that gives each page its own variant, a variant pinned to one page
 * that the run keeps, and the strip that marks and filters the pages by variant.
 */

const PAGES = 4;
const PLATE_POSITION = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const DESKEW_TITLE = 'Deskew';
// The recipes a new book starts with, and the places of the second of them in the list of the recipes
const TEXT_VARIANT = 'Text';
const PLATES_VARIANT = 'Plates';
const DEFAULT_RECIPES = 3;
const PLATES_INDEX = 1;

// Tall enough for the pictures of the key states to show two pages of the strip and a section of the panel
test.use({ viewport: { width: 1280, height: 1000 } });

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

test('plates get their own variant by a rule, a pinned variant survives a run on all pages, and the strip marks them', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const strip = page.getByTestId('strip-page');
  const marks = page.getByTestId('strip-variant');
  const runAll = async (): Promise<void> => {
    await runAllPages(page);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
  };
  let bookPath = '';
  // The names of the two variants, read from the book once it is open: the active recipe, and the copy made of it
  let textVariant = '';
  const platesVariant = PLATES_VARIANT;

  await test.step('a book with a plate among its pages opens on the Geometry stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with plates');
    await uploadFolder(page, folder, PAGES);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await setKind(page, PLATE_POSITION, 'plate');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(strip).toHaveCount(PAGES);
    // A new book starts with the recipes Text, Plates and Flat, in the order of their templates, and the first is active
    await expect(page.getByTestId('recipe-select').locator('option')).toHaveCount(DEFAULT_RECIPES);
    textVariant = await activeRecipeName(page, 'geometry');
    expect(textVariant).toBe(TEXT_VARIANT);
  });

  await test.step('the variant Plates has the settings of its own, and a rule sends the plates and frontispieces to it', async () => {
    await page.getByTestId('recipe-select').selectOption({ index: PLATES_INDEX });
    await page.getByTestId('bar-step').filter({ hasText: DESKEW_TITLE }).click();
    // The steps of a recipe are drawn by the method they name, and Plates levels the page by its long straight lines
    await expect(
      page.getByTestId('step-panel-settings').getByRole('spinbutton', { name: 'Shortest line' }),
    ).toBeVisible();
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

    // The run that pinned the variant is over once the menu of the run is free again
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
