import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Locator, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  writeSheetsFolder,
} from './support/account';

/**
 * The compare of a step that puts its input on another page: on Margins, the block of text that the step read lies on the
 * page the step made, in the place its frame says, at the scale of the page, whatever the mode of the compare is.
 *
 * The canvas writes where each picture stands in the world into `data-before-box` and `data-after-box` of its element, as
 * the left, the top, the width and the height in page heights. The block of the input is the picture before and the page of the
 * book is the picture after, and the scenario checks them against the frame and the sizes the server holds for the step.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const PAGES = 1;
const PRECISION_DIGITS = 2;
const NORMALIZE = 'geometry.normalize';

/** What the server holds about the open step on the page: the size of its input and of its page, and where the block lies. */
interface StepFacts {
  input: { width: number; height: number };
  page: { width: number; height: number };
  frame: { left: number; top: number; width: number; height: number };
}

/** Read the numbers a canvas writes into an attribute, as the left, the top, the width and the height. */
async function boxOf(canvas: Locator, attribute: string): Promise<number[]> {
  await expect(canvas).toHaveAttribute(attribute, /^-?[\d.]+(,-?[\d.]+){3}$/);
  return ((await canvas.getAttribute(attribute)) ?? '').split(',').map(Number);
}

test('the compare of Margins draws the block of text inside the page at the place of its frame, in the swipe and side by side', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  let facts: StepFacts | null = null;

  await test.step('the stage runs on a scan, so that Margins has a block and a page', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of one sheet');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(page.getByTestId('this-page-facts')).toContainText('Confidence', {
      timeout: RUN_TIMEOUT_MS,
    });
    // The sheet is taken for a colour picture, so the page was made by the recipe of Plates, which the stage shows once it is in use
    const plates = await page
      .getByTestId('recipe-select')
      .locator('option', { hasText: /^Plates/ })
      .getAttribute('value');
    await page.getByTestId('recipe-select').selectOption(plates ?? '');
    await page.getByTestId('recipe-use').click();
    await expect(page.getByTestId('recipe-active')).toBeVisible();
    const [stepId = ''] = await stepIdsOf(page, 'geometry', NORMALIZE);
    const stepRow = async () => {
      const listed = await page.request.get(
        `/api/v1/projects/${openProjectId(page)}/stages/geometry/pages?step=${stepId}&size=10`,
      );
      const [item] = (
        (await listed.json()) as {
          items: {
            step: {
              input_version: { data: Record<string, number> } | null;
              version: { data: Record<string, number | Record<string, number>> } | null;
            };
          }[];
        }
      ).items;
      return item?.step;
    };
    // The run is over when the step has made its version on the page
    await expect
      .poll(async () => (await stepRow())?.version ?? null, { timeout: RUN_TIMEOUT_MS })
      .not.toBeNull();
    const item = { step: await stepRow() };
    const input = item.step?.input_version?.data ?? {};
    const made = item.step?.version?.data ?? {};
    const frame = made.frame as Record<string, number>;
    facts = {
      input: { width: input.width_px ?? 0, height: input.height_px ?? 0 },
      page: { width: made.width_px as number, height: made.height_px as number },
      frame: {
        left: frame.left ?? 0,
        top: frame.top ?? 0,
        width: frame.width ?? 0,
        height: frame.height ?? 0,
      },
    };
    // The margins put the block inside the page, so its place is not the whole page
    expect(facts.frame.left).toBeGreaterThan(0);
    expect(facts.frame.width).toBeLessThan(facts.page.width);
    await page.goto(`${bookPath}/stages/geometry/steps/${stepId}?compare=swipe`);
    await expect(canvas).toHaveAttribute('data-state', 'ready', { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('the page stands whole and the block lies inside it where the frame is, at the scale of the page', async () => {
    if (facts === null) {
      throw new Error('The step facts were not read.');
    }
    const { page: size, frame, input } = facts;
    const [pageLeft = NaN, pageTop = NaN, pageWidth = NaN, pageHeight = NaN] = await boxOf(
      canvas,
      'data-after-box',
    );
    const [left = NaN, top = NaN, width = NaN, height = NaN] = await boxOf(
      canvas,
      'data-before-box',
    );
    expect([pageLeft, pageTop, pageHeight]).toEqual([0, 0, 1]);
    expect(pageWidth).toBeCloseTo(size.width / size.height, PRECISION_DIGITS);
    // A pixel of the page is the same length in the world whichever picture it is on
    expect(left).toBeCloseTo((frame.left / size.width) * pageWidth, PRECISION_DIGITS);
    expect(top).toBeCloseTo(frame.top / size.height, PRECISION_DIGITS);
    expect(height).toBeCloseTo(frame.height / size.height, PRECISION_DIGITS);
    expect(width).toBeCloseTo((frame.width / size.width) * pageWidth, PRECISION_DIGITS);
    // The block keeps its own shape
    expect(width / height).toBeCloseTo(input.width / input.height, PRECISION_DIGITS);
    await snap(page, 'compare-placement-swipe');
  });

  await test.step('side by side keeps the two pictures where they were, so the views show the same part of the page', async () => {
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-side').click();
    await expect(page).toHaveURL(/compare=side/);
    await expect(page.getByTestId('viewer-canvas-after')).toBeVisible();
    const before = await boxOf(canvas, 'data-before-box');
    const after = await boxOf(canvas, 'data-after-box');
    expect(before[2]).toBeLessThan(after[2] ?? 0);
    await snap(page, 'compare-placement-side');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
