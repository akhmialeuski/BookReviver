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
  writeMixedFolder,
} from './support/account';

/**
 * The content type of a page: the program finds text, a colour picture or a black-and-white one on each page as the
 * pages are made, the strip marks every page with it, the selected pages are changed at once and given back to the
 * program, and the steps of the first recipes process the pages they are for.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const DETECT_TIMEOUT_MS = 90_000;
const PAGES = 4;
const TEXT_POSITION = 0;
const COLOUR_POSITION = 1;
const BW_POSITION = 2;
const LAST_POSITION = 3;

// Tall enough for the pictures of the key states to show the marks of the strip and the section of the panel
test.use({ viewport: { width: 1280, height: 1000 } });

interface ListedPage {
  position: number;
  content_type: string;
  content_source: string;
}

/** Read what each page of the open book shows, in book order. */
async function contentOf(page: Page): Promise<string[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: ListedPage[] }).items;
  return items
    .toSorted((a, b) => a.position - b.position)
    .map((item) => `${item.content_type}:${item.content_source}`);
}

/**
 * Read the conditions of the first steps of the recipe in the panel. A step shows its condition among its settings, which
 * its toggle opens, and the first step of a recipe is open already, so a step is opened only when it is closed.
 */
async function conditionsOf(page: Page, count: number): Promise<string[]> {
  const conditions: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const step = page.getByTestId('recipe-step').nth(index);
    const toggle = step.getByTestId('step-toggle');
    if ((await toggle.getAttribute('aria-expanded')) !== 'true') {
      await toggle.click();
    }
    conditions.push(await step.getByTestId('step-condition').inputValue());
  }
  return conditions;
}

test('the program finds what each page shows, the strip marks it, and the selected pages are changed and given back at once', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeMixedFolder();
  const marks = page.getByTestId('strip-content');
  const select = page.getByTestId('content-type-select');

  await test.step('the pages made from the scans are detected without being asked', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with plates');
    await uploadFolder(page, folder, PAGES);
    await expect
      .poll(() => contentOf(page), { timeout: DETECT_TIMEOUT_MS })
      .toEqual(['text:detected', 'color-picture:detected', 'bw-picture:detected', 'text:detected']);
    await waitForIdleJobs(page, openProjectId(page));
  });

  await test.step('the strip of the Geometry stage marks each page with what it shows', async () => {
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(marks).toHaveCount(PAGES);
    await expect(marks.nth(TEXT_POSITION)).toHaveAttribute('data-content', 'text');
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-content', 'color-picture');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute('data-content', 'bw-picture');
    await expect(marks.nth(COLOUR_POSITION)).toContainText('Colour');
    await expect(marks.nth(BW_POSITION)).toContainText('B/W');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute(
      'title',
      'Black-and-white picture · Found by the program',
    );
    await snap(page, 'content-type-marks-in-the-strip');
  });

  await test.step('the steps of the first recipes process the pages they are for', async () => {
    await expect(page.getByTestId('recipe-step').first()).toBeVisible();
    // Perspective, Deskew, Dewarp, Select content and Margins: the two that follow the lines of text are for text
    expect(await conditionsOf(page, 5)).toEqual(['all', 'text', 'text', 'all', 'all']);
  });

  await test.step('two selected pages of different types are changed to text at once', async () => {
    await page.getByTestId('strip-view-switch').click();
    await expect(page).toHaveURL(/view=grid/);
    const tiles = page.getByTestId('strip-page');
    await tiles.nth(COLOUR_POSITION).click();
    await tiles.nth(BW_POSITION).click({ modifiers: ['ControlOrMeta'] });
    await expect(page.getByTestId('grid-selection')).toHaveText('2 pages selected');
    await expect(select).toHaveValue('');
    await expect(page.getByTestId('content-type-pages')).toHaveText('2 selected pages');
    await snap(page, 'content-type-two-pages-selected');

    await select.selectOption('text');
    await expect
      .poll(() => contentOf(page))
      .toEqual(['text:detected', 'text:hand', 'text:hand', 'text:detected']);
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-content', 'text');
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-source', 'hand');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute('data-source', 'hand');
    await expect(select).toHaveValue('text');
    await expect(page.getByTestId('content-type-sources')).toHaveText(
      '0 found by the program · 2 set by hand',
    );
    await snap(page, 'content-type-set-by-hand');
  });

  await test.step('the pages set by hand are given back to the program, which finds the pictures again', async () => {
    await page.getByTestId('content-type-detect').click();
    await expect
      .poll(() => contentOf(page), { timeout: DETECT_TIMEOUT_MS })
      .toEqual(['text:detected', 'color-picture:detected', 'bw-picture:detected', 'text:detected']);
    await expect(marks.nth(COLOUR_POSITION)).toHaveAttribute('data-source', 'detected');
    await expect(marks.nth(BW_POSITION)).toHaveAttribute('data-content', 'bw-picture');
    await snap(page, 'content-type-detected-again');
  });

  await test.step('a single open page is changed without a selection', async () => {
    await page.getByRole('button', { name: 'Clear the selection' }).click();
    await page.getByTestId('strip-page').nth(LAST_POSITION).dblclick();
    await expect(page).not.toHaveURL(/view=grid/);
    await expect(select).toHaveValue('text');
    await select.selectOption('bw-picture');
    await expect
      .poll(() => contentOf(page))
      .toEqual([
        'text:detected',
        'color-picture:detected',
        'bw-picture:detected',
        'bw-picture:hand',
      ]);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('the first recipe of the Cleanup stage keeps binarization and despeckling off the pictures', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeMixedFolder();

  await registerAndSignIn(page);
  await createBook(page, 'A book to clean');
  await uploadFolder(page, folder, PAGES);
  // The split and the detection that follow the import read the recipes again, which closes an open step
  await waitForIdleJobs(page, openProjectId(page));
  const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
  await page.goto(`${bookPath}/stages/cleanup`);
  await expect(page.getByTestId('stage-title')).toHaveText('Cleanup');
  const steps = page.getByTestId('recipe-step');
  await expect(steps.first()).toBeVisible();

  const processors = await Promise.all(
    [0, 1, 2].map((index) => steps.nth(index).getAttribute('data-processor')),
  );
  const conditions = await conditionsOf(page, 3);
  expect(processors).toEqual(['cleanup.binarize', 'cleanup.despeckle', 'cleanup.eraser']);
  expect(conditions).toEqual(['text', 'text', 'all']);
  await snap(page, 'content-type-cleanup-conditions');

  await rm(path.dirname(folder), { recursive: true, force: true });
});
