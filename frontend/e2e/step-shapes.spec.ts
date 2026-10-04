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
import { numbersOf, pairOf } from './support/layer';

/**
 * The shape of a step on the page, in its three states, and the grid over the page.
 *
 * A step that is open has its shape on the page from the start: the grey dashed default before the step has run, the green
 * line it found after "Auto on all pages", and the orange line the reader set, which the later runs keep. The angle of the
 * Deskew step is set with the field, the slider and the arrow keys, and all three states are still there when the open
 * step changes and comes back. The grid is a choice of the book that every step of Geometry shares, and the key G switches
 * it.
 *
 * The pages are plain squares, so the steps find nothing on their own and the shapes they find are the ones they start
 * from. Exact values come from the field and from the keys and never from a drag, so no screen distance depends on the
 * size of the page on the canvas.
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 90_000;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;

// Tall enough for the pictures of the key states to show the bar, the canvas and the section of the step in the panel
test.use({ viewport: { width: 1280, height: 1000 } });

/** Count the runs of a stage that ended well in the open book, which tells that a run the reader started is over. */
async function finishedRuns(page: Page): Promise<number> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=50`);
  const items = ((await listed.json()) as { items: { kind: string; state: string }[] }).items;
  return items.filter((job) => job.kind === 'run-stage' && job.state === 'succeeded').length;
}

test('the shape of a step is on the page in its three states, they survive a change of step, and the grid is the book’s', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const barSteps = page.getByTestId('bar-step');
  const layer = page.getByTestId('editor-layer');
  const grid = page.getByTestId('canvas-grid');
  const gridButton = page.getByTestId('canvas-grid-toggle');
  const state = page.getByTestId('step-panel-state');
  const hint = page.getByTestId('step-panel-hint');
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
    await registerAndSignIn(page);
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
    await expect(hint).toContainText('Dashed grey');
    const right = await pairOf(layer, 'data-handle-rotation');
    const left = await pairOf(layer, 'data-handle-rotation-left');
    expect(right.x).toBeGreaterThan(left.x);
    expect(Math.abs(right.y - left.y)).toBeLessThan(1);
    await snap(page, 'deskew-default-shape');
  });

  await test.step('the grid is on at Deskew until the reader chooses, and the key G and the button switch it', async () => {
    await expect(grid).toBeVisible();
    await expect(gridButton).toHaveAttribute('aria-pressed', 'true');
    await snap(page, 'deskew-grid-on');

    await page.keyboard.press('g');
    await expect(grid).toHaveCount(0);
    await expect(gridButton).toHaveAttribute('aria-pressed', 'false');
    await page.keyboard.press('Shift+G');
    await expect(grid).toBeVisible();

    await gridButton.click();
    await expect(grid).toHaveCount(0);
  });

  await test.step('the choice of the book stays through a reload and goes with the reader to every step of Geometry', async () => {
    await page.reload();
    await expect(page.getByTestId('step-panel-title')).toHaveText(/Deskew/);
    // The grid is drawn with the canvas, so the canvas is waited for before the grid is said to be gone
    await expect(layer).toBeVisible();
    await expect(grid).toHaveCount(0);

    await open('Perspective', 'Corners of the sheet');
    await expect(grid).toHaveCount(0);
    await expect(layer).toHaveAttribute('data-figure', 'default');
    await gridButton.click();
    await expect(grid).toBeVisible();
    await snap(page, 'perspective-default-shape-with-grid');

    await open('Select content', 'Frame of the content');
    await expect(layer).toHaveAttribute('data-figure', 'default');
    await expect(grid).toBeVisible();
    const [, , width = 0, height = 0] = await numbersOf(layer, 'data-rect');
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);

    await open('Deskew', 'Page rotation');
    await expect(grid).toBeVisible();
  });

  await test.step('"Auto on all pages" gives the steps their found shape, green on the page and named in the panel', async () => {
    const before = await finishedRuns(page);
    await page.getByTestId('step-auto').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
    await expect(layer).toHaveAttribute('data-figure', 'found');
    await expect(state).toContainText('Found by the step');
    await expect(hint).toContainText('Green');
    await expect(stepOf('Deskew')).toHaveAttribute('data-state', 'found');
    await snap(page, 'deskew-found-shape');

    await open('Perspective', 'Corners of the sheet');
    await expect(layer).toHaveAttribute('data-figure', 'found');
    await open('Deskew', 'Page rotation');
  });

  await test.step('the angle typed in the field is the shape set by hand, orange, and the bar says so', async () => {
    await angle.fill('1.5');
    await angle.press('Enter');
    await expect(layer).toHaveAttribute('data-degrees', '1.5');
    await settled();
    await expect(state).toContainText('Set by hand');
    await expect(hint).toContainText('Orange');
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
    await page.getByTestId('editor-auto').click();
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
    await waitForIdleJobs(page, openProjectId(page));
    const before = await finishedRuns(page);
    await expect(page.getByTestId('step-auto-page')).toBeEnabled();
    await page.getByTestId('step-auto-page').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    await expect(layer).toHaveAttribute('data-degrees', '2');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
