import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openLibrary,
  openProjectId,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
  writeSheetsFolder,
} from './support/account';
import { test } from './support/fixtures';
import { dragFrom, numbersOf, pairOf } from './support/layer';
import { runPages } from './support/page-work';

/**
 * The shape of a step on the page, in its three states.
 *
 * A step that is open has its shape on the page from the start: the grey dashed default before the step has run, the green
 * line it found after a run of all pages, and the thicker dark green line the reader set, which the later runs keep. The angle of the
 * Deskew step is set with the field, the slider and the arrow keys, and all three states are still there when the open
 * step changes and comes back. The grid over the page is the subject of `grid-geometry-steps.spec.ts`.
 *
 * The pages are plain squares, so the steps find nothing on their own and the shapes they find are the ones they start
 * from. Exact values come from the field and from the keys and never from a drag, so no screen distance depends on the
 * size of the page on the canvas.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 90_000;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;
// Outward, into the margin of the scan, so that the corner of the sheet stays inside the picture
const CORNER_DRAG = { x: -12, y: -9 };

// Tall enough for the pictures of the key states to show the bar, the canvas and the section of the step in the panel
test.use({ viewport: { width: 1280, height: 1000 } });

test('the shape of a step is on the page in its three states, and they survive a change of step', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const barSteps = page.getByTestId('bar-step');
  const layer = page.getByTestId('editor-layer');
  // The open step names the state of its shape in the bar
  const state = page.locator('[data-testid="bar-step"][aria-current="step"]');
  const angle = page.getByRole('textbox', { name: 'Angle in degrees' });
  const slider = page.getByRole('slider', { name: 'Angle of the page' });
  const stepOf = (title: string) => barSteps.filter({ hasText: title }).first();

  /** Open a step from the bar and wait for its picture, so that its shape can be on it. */
  async function open(title: string, label: string): Promise<void> {
    await stepOf(title).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(layer).toHaveAttribute('aria-label', label);
  }

  /** Wait until the saved edit of the open step is on the screen as set by hand, which a run on the page follows. */
  async function settled(): Promise<void> {
    await expect(layer).toHaveAttribute('data-figure', 'by-hand', { timeout: RUN_TIMEOUT_MS });
    await waitForIdleJobs(page, openProjectId(page));
  }

  await test.step('a book of three pages opens on Geometry with a step bar', async () => {
    await openLibrary(page);
    await createBook(page, 'A book of shapes');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(barSteps.first()).toBeVisible();
  });

  await test.step('Deskew opens with its axis on the page before it has run: grey and dashed, level, a handle at each end', async () => {
    await open('Deskew', 'Page rotation');
    await expect(layer).toHaveAttribute('data-figure', 'default');
    await expect(layer).toHaveAttribute('data-degrees', '0');
    await expect(state).toContainText('Default shape');
    const right = await pairOf(layer, 'data-handle-rotation');
    const left = await pairOf(layer, 'data-handle-rotation-left');
    expect(right.x).toBeGreaterThan(left.x);
    expect(Math.abs(right.y - left.y)).toBeLessThan(1);
    await snap(page, 'deskew-default-shape');
  });

  await test.step('Perspective and Select content open with their default shape on the page before they have run', async () => {
    await open('Perspective', 'Corners of the sheet');
    await expect(layer).toHaveAttribute('data-figure', 'default');
    await snap(page, 'perspective-default-shape');

    await open('Select content', 'Frame of the content');
    await expect(layer).toHaveAttribute('data-figure', 'default');
    const [, , width = 0, height = 0] = await numbersOf(layer, 'data-rect');
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);

    await open('Deskew', 'Page rotation');
  });

  await test.step('a run of all pages through the open step gives the steps their found shape, green on the page and named in the bar', async () => {
    await runPages(page, { throughOpenStep: true });
    await expect(layer).toHaveAttribute('data-figure', 'found');
    await expect(state).toContainText('Found by the step');
    await expect(stepOf('Deskew')).toHaveAttribute('data-state', 'found');
    await snap(page, 'deskew-found-shape');

    await open('Perspective', 'Corners of the sheet');
    await expect(layer).toHaveAttribute('data-figure', 'found');
    await open('Deskew', 'Page rotation');
  });

  await test.step('the angle typed in the field is the shape set by hand, dark green, and the bar says so', async () => {
    await angle.fill('1.5');
    await angle.press('Enter');
    await expect(layer).toHaveAttribute('data-degrees', '1.5');
    await settled();
    await expect(state).toContainText('Set by hand');
    await expect(stepOf('Deskew')).toHaveAttribute('data-state', 'by-hand');
    await snap(page, 'deskew-set-by-hand');
  });

  await test.step('the arrow keys turn the page by 0.05 degrees once the editor has the focus', async () => {
    await layer.focus();
    await page.keyboard.press('ArrowRight');
    await expect(layer).toHaveAttribute('data-degrees', '1.55');
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expect(layer).toHaveAttribute('data-degrees', '1.45');
    await settled();
    await expect(angle).toHaveValue('1.45');
  });

  await test.step('the slider turns it by the same step, and 0° lays the page level', async () => {
    await slider.focus();
    await page.keyboard.press('ArrowRight');
    await expect(layer).toHaveAttribute('data-degrees', '1.5');
    await settled();

    await page.getByTestId('angle-zero').click();
    await expect(layer).toHaveAttribute('data-degrees', '0');
    await settled();
    await expect(page.getByTestId('angle-zero')).toBeDisabled();
  });

  await test.step('Auto takes the shape set by hand away and gives back the one the step found', async () => {
    await page.getByTestId('canvas-auto').click();
    await expect(layer).toHaveAttribute('data-figure', 'found', { timeout: RUN_TIMEOUT_MS });
    await expect(state).toContainText('Found by the step');
    await waitForIdleJobs(page, openProjectId(page));
  });

  await test.step('a shape set by hand stays as the open step changes and comes back, and a reload does not lose it', async () => {
    await angle.fill('2');
    await angle.press('Enter');
    await settled();

    await open('Perspective', 'Corners of the sheet');
    await expect(layer).toHaveAttribute('data-figure', 'found');
    await expect(stepOf('Deskew')).toHaveAttribute('data-state', 'by-hand');

    await open('Deskew', 'Page rotation');
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    await expect(layer).toHaveAttribute('data-degrees', '2');
    await expect(state).toContainText('Set by hand');

    await page.reload();
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    await expect(layer).toHaveAttribute('data-degrees', '2');
  });

  await test.step('detecting again on the open page keeps the shape the reader set', async () => {
    await runPages(page, { pages: 'page', throughOpenStep: true });
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    await expect(layer).toHaveAttribute('data-degrees', '2');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('on a sheet of old paper the shape set by hand stays a dark green line beside the one the step found', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(1);
  const layer = page.getByTestId('editor-layer');

  await test.step('a sheet of yellowed paper is run through Geometry, and Perspective found its corners', async () => {
    await openLibrary(page);
    await createBook(page, 'A book of old paper');
    await uploadFolder(page, folder, 1);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await runPages(page);
    await page.getByTestId('bar-step').filter({ hasText: 'Perspective' }).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(layer).toHaveAttribute('data-figure', 'found', { timeout: RUN_TIMEOUT_MS });
    await snap(page, 'paper-found');
  });

  await test.step('a corner dragged by hand turns the shape into the thicker dark green one', async () => {
    await dragFrom(page, layer, await pairOf(layer, 'data-handle-top-left'), CORNER_DRAG);
    await expect(layer).toHaveAttribute('data-figure', 'by-hand', { timeout: RUN_TIMEOUT_MS });
    await waitForIdleJobs(page, openProjectId(page));
    await snap(page, 'paper-set-by-hand');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
