import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writeSheetsFolder,
} from './support/account';
import { dragFrom, numbersOf, pairOf } from './support/layer';
import { runPages } from './support/page-work';

/**
 * The Geometry stage on scans of a sheet of paper laid on a dark binding: the steps of the default recipe find the sheet,
 * level the lines, flatten them, cut the page to the frame of its words and put the block on a page of the book, and the
 * reader corrects the sheet, the angle and the frame on the canvas of one page. The curves of the flattening have a
 * scenario of their own in `dewarp.spec.ts`, and the block on the page in `normalize.spec.ts`.
 *
 * Each correction is saved when a handle is let go, the stage runs again on that page, and the panel names the step as set
 * by hand. The three corrections survive a run of the stage on all pages, and "Auto" takes them away one by one.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const PAGES = 2;
// The steps of the default recipe: the sheet, the angle, the curves, the frame and the block on the page
const STEPS = 5;
// Outward, into the margin of the scan: a drag inward by screen pixels cuts into the text at a smaller zoom, and the frame
// the later step finds would then touch the edge of the page
const SHEET_DRAG = { x: -12, y: -9 };
const FRAME_DRAG_PX = 30;
const ANGLE_DEG = '1.5';

// The titles of the steps in the bar
const SHEET = 'Perspective';
const ANGLE = 'Deskew';
const FRAME = 'Select content';

/** Run the stage on all pages and wait until every page is up to date. */
async function runAll(page: Page): Promise<void> {
  await runPages(page);
  await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
    timeout: RUN_TIMEOUT_MS,
  });
}

test('a reader corrects the sheet, the angle and the frame of a page, and the corrections outlive a run on all pages', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(PAGES);
  const layer = page.getByTestId('editor-layer');
  const facts = page.getByTestId('this-page-facts');
  const steps = page.getByTestId('bar-step');
  const stepOf = (title: string) => steps.filter({ hasText: title });
  // An edit starts a run of the stage on the page, and the next change waits until that run is over
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0);
    await waitForIdleJobs(page, openProjectId(page));
  };
  const saves: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/geometry/')) {
      saves.push(request.url().replace(/^.*\/edits\/geometry\//, ''));
    }
  });
  let sheet = { x: 0, y: 0 };
  let frame: number[] = [];

  await test.step('the stage runs on the scans and gives each page its sheet, its angle and its frame', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of sheets');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(page.getByTestId('bar-step')).toHaveCount(STEPS);
    await runAll(page);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(facts).toContainText('Confidence', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('Automatic');
    await expect(steps).toHaveCount(STEPS);
  });

  await test.step('the step of the sheet shows its corners as the step found them, over the scan', async () => {
    await stepOf(SHEET).click();
    await expect(layer).toBeVisible();
    await expect(layer).toHaveAttribute('aria-label', 'Corners of the sheet');
    await expect(stepOf(SHEET)).toHaveAttribute('data-open', 'true');
    sheet = await pairOf(layer, 'data-corner-top-left');
    // The sheet lies inside the scan, so a corner is not on the edge of the picture
    expect(sheet.x).toBeGreaterThan(20);
    expect(sheet.y).toBeGreaterThan(20);
    await snap(page, 'geometry-sheet-quad');
  });

  await test.step('dragging a corner saves the corners of the user and straightens the page again', async () => {
    const handle = await pairOf(layer, 'data-handle-top-left');
    await dragFrom(page, layer, handle, SHEET_DRAG);
    await expect(layer).not.toHaveAttribute('data-corner-top-left', `${sheet.x},${sheet.y}`);
    await expect(stepOf(SHEET)).toHaveAttribute('data-state', 'by-hand', {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(facts).toContainText('By hand', { timeout: RUN_TIMEOUT_MS });
    await settled();
    const [perspective = ''] = await stepIdsOf(page, 'geometry', 'geometry.perspective');
    expect(saves).toEqual([perspective]);
    sheet = await pairOf(layer, 'data-corner-top-left');
  });

  await test.step('the angle is set on the picture the sheet step made, and the corners stay', async () => {
    await stepOf(ANGLE).click();
    await expect(layer).toHaveAttribute('aria-label', 'Page rotation');
    const field = page.getByRole('textbox', { name: 'Angle in degrees' });
    await field.fill(ANGLE_DEG);
    await field.press('Enter');
    await expect(layer).toHaveAttribute('data-degrees', ANGLE_DEG);
    await expect(stepOf(ANGLE)).toHaveAttribute('data-state', 'by-hand', {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(facts).toContainText(`${ANGLE_DEG}°`, { timeout: RUN_TIMEOUT_MS });
    await settled();
    const [perspective = ''] = await stepIdsOf(page, 'geometry', 'geometry.perspective');
    const [deskew = ''] = await stepIdsOf(page, 'geometry', 'geometry.deskew');
    expect(saves).toEqual([perspective, deskew]);
  });

  await test.step('the frame is shown on the page after the first two steps, with a handle on each corner and side', async () => {
    await stepOf(FRAME).click();
    await expect(layer).toHaveAttribute('aria-label', 'Frame of the content');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    frame = await numbersOf(layer, 'data-rect');
    const [left = 0, top = 0, width = 0, height = 0] = frame;
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);
    // The frame holds the words, which stand inside the sheet
    expect(left).toBeGreaterThan(0);
    expect(top).toBeGreaterThan(0);
    await snap(page, 'geometry-content-frame');
  });

  await test.step('dragging a side narrows the frame, saves it, and Ctrl+Z takes it back', async () => {
    const side = await pairOf(layer, 'data-handle-right');
    await dragFrom(page, layer, side, { x: -FRAME_DRAG_PX, y: 0 });
    const narrowed = await numbersOf(layer, 'data-rect');
    expect(narrowed[2] ?? 0).toBeLessThan(frame[2] ?? 0);
    await expect(stepOf(FRAME)).toHaveAttribute('data-state', 'by-hand', {
      timeout: RUN_TIMEOUT_MS,
    });
    expect(saves.at(-1)).toBe((await stepIdsOf(page, 'geometry', 'geometry.crop'))[0]);
    await settled();

    await layer.focus();
    await page.keyboard.press('Control+z');
    await expect(stepOf(FRAME)).toHaveAttribute('data-state', 'found', {
      timeout: RUN_TIMEOUT_MS,
    });
    await settled();
    await expect(layer).toHaveAttribute('data-rect', frame.join(','));
  });

  await test.step('the frame is set again, and a run on all pages keeps the three corrections', async () => {
    const side = await pairOf(layer, 'data-handle-bottom');
    await dragFrom(page, layer, side, { x: 0, y: -FRAME_DRAG_PX });
    await expect(stepOf(FRAME)).toHaveAttribute('data-state', 'by-hand', {
      timeout: RUN_TIMEOUT_MS,
    });
    frame = await numbersOf(layer, 'data-rect');
    await settled();

    await runAll(page);
    await expect(steps).toHaveCount(STEPS, { timeout: RUN_TIMEOUT_MS });
    for (const title of [SHEET, ANGLE, FRAME]) {
      await expect(stepOf(title)).toHaveAttribute('data-state', 'by-hand');
    }
    await expect(layer).toHaveAttribute('data-rect', frame.join(','));
    await stepOf(SHEET).click();
    await expect(layer).toHaveAttribute('data-corner-top-left', `${sheet.x},${sheet.y}`);
    await stepOf(ANGLE).click();
    await expect(layer).toHaveAttribute('data-degrees', ANGLE_DEG);
    await expect(facts).toContainText('By hand');
  });

  await test.step('Auto takes the corrections away one step at a time and the steps find the result by themselves', async () => {
    for (const title of [SHEET, ANGLE, FRAME]) {
      await stepOf(title).click();
      await page.getByTestId('canvas-auto').click();
      await expect(stepOf(title)).toHaveAttribute('data-state', 'found', {
        timeout: RUN_TIMEOUT_MS,
      });
      await settled();
    }
    await expect(page.getByTestId('canvas-auto')).toHaveCount(0);
    await expect(facts).toContainText('Automatic', { timeout: RUN_TIMEOUT_MS });
    // The sheet, the angle and two frames were saved, and the first frame was taken back by Ctrl+Z
    expect(saves).toHaveLength(4);
  });

  await test.step('the page after the four steps is shown with the editor shut', async () => {
    // The shape of an open step gives way to the compare, so the last picture is the one the step made
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-swipe').click();
    await expect(layer).toHaveCount(0);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await snap(page, 'geometry-after-four-steps');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
