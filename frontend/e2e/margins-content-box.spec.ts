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
  writeScaledSheetsFolder,
} from './support/account';
import { dragFrom, numbersOf, pairOf } from './support/layer';
import { countEdits, pageIds, readSettings } from './support/page-work';

/**
 * The content box and the border of the page on the Margins step: both are found when the step is opened on a page that has
 * not been run, the box is dragged by its handles, a side of the border sets a margin of that page, the alignment is chosen
 * in the panel, and a run on all pages gives every page one size without the book being measured while it keeps the box and
 * the margin of the page the reader worked on. "Auto" takes the box away and the step finds it again.
 *
 * The two scans show the same kind of sheet at two scales of the text, so the blocks of the two pages differ in size and a
 * page of the size of its own block would differ from the other.
 */

const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 120_000;
const SCALES = [1, 1.2] as const;
const PAGES = SCALES.length;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;
const DRAG_PX = 30;
// How far a box found by a run of a page may stand from the one a preview found, in pixels of the picture
const FOUND_TOLERANCE_PX = 12;
// What the margins of the step are in the form, in millimetres, which the scans of this scenario meet unchanged
const MARGIN_FIELDS = [
  ['Top margin, mm', '10'],
  ['Bottom margin, mm', '15'],
  ['Inner margin, mm', '15'],
  ['Outer margin, mm', '10'],
] as const;
const SIDES = ['left', 'right', 'top', 'bottom'] as const;
// The height the toolbar over the bottom of the canvas covers, counted from the bottom edge
const TOOLBAR_PX = 64;
const LEFT_MARGIN_SETTINGS = ['margin_inner', 'margin_outer'];

// Tall enough for the pictures of the key states to show the bar, the canvas and the panel
test.use({ viewport: { width: 1280, height: 1000 } });

/** The size of the page a stage made, as the server holds it. */
interface Size {
  width: number;
  height: number;
}

/** Read the size of the current result of the Geometry stage of every page of the book, in the order of the book. */
async function sizesOf(page: Page, projectId: string): Promise<Size[]> {
  const sizes: Size[] = [];
  for (const id of await pageIds(page)) {
    const stages = (await (
      await page.request.get(`/api/v1/projects/${projectId}/pages/${id}/stages`)
    ).json()) as { items: { stage: string; head_version_id: string }[] };
    const head = stages.items.find((item) => item.stage === 'geometry')?.head_version_id;
    const version = (await (
      await page.request.get(`/api/v1/projects/${projectId}/pages/${id}/versions/${head}`)
    ).json()) as { data: { width_px: number; height_px: number } };
    sizes.push({ width: version.data.width_px, height: version.data.height_px });
  }
  return sizes;
}

/** Run the stage on all pages and wait until every page is up to date. */
async function runAll(page: Page): Promise<void> {
  await page.getByTestId('run-menu').click();
  await page.getByTestId('run-all').click();
  await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
    timeout: RUN_TIMEOUT_MS,
  });
}

test('the content box and the border of a page are found on opening, edited with the mouse, and kept by a run on all pages that gives every page one size', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScaledSheetsFolder(SCALES);
  const layer = page.getByTestId('editor-layer');
  const canvas = page.getByTestId('viewer-canvas');
  const settingSaves: string[] = [];
  let projectId = '';
  let found: number[] = [];
  let boxByHand: number[] = [];

  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/settings/geometry/')) {
      settingSaves.push(request.url());
    }
  });
  // An edit or a setting starts a run of the stage on the page, and the next change waits until that run is over
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0, { timeout: RUN_TIMEOUT_MS });
    await waitForIdleJobs(page, projectId);
  };

  await test.step('a book with two sheets of text opens on Geometry, with no page run yet', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of margins');
    await uploadFolder(page, folder, PAGES);
    projectId = openProjectId(page);
    await waitForIdleJobs(page, projectId);
    await markPagesAsText(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(page.getByTestId('bar-step').filter({ hasText: 'Margins' })).toBeVisible();
  });

  await test.step('Margins opened on a page that was not run shows the content box and the border as found', async () => {
    await page.getByTestId('bar-step').filter({ hasText: 'Margins' }).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(layer).toHaveAttribute('data-figure', 'found', { timeout: RUN_TIMEOUT_MS });
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    found = await numbersOf(layer, 'data-rect');
    const outer = await numbersOf(layer, 'data-outer');
    const [left = 0, top = 0, width = 0, height = 0] = found;
    const [outerLeft = 0, outerTop = 0, outerWidth = 0, outerHeight = 0] = outer;
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);
    // The border lies outside the box on every side
    expect(outerLeft).toBeLessThan(left);
    expect(outerTop).toBeLessThan(top);
    expect(outerLeft + outerWidth).toBeGreaterThan(left + width);
    expect(outerTop + outerHeight).toBeGreaterThan(top + height);
    await expect(page.getByTestId('editor-auto')).toBeDisabled();
    await snap(page, 'margins-found-on-open');
  });

  await test.step('dragging a corner of the content box saves it for the page and places the page again', async () => {
    const corner = await pairOf(layer, 'data-handle-bottom-right');
    await dragFrom(page, layer, corner, { x: -DRAG_PX, y: -DRAG_PX });
    await expect(layer).toHaveAttribute('data-figure', 'by-hand', { timeout: RUN_TIMEOUT_MS });
    await settled();
    boxByHand = await numbersOf(layer, 'data-rect');
    expect(boxByHand[2]).toBeLessThan(found[2] ?? 0);
    expect(boxByHand[3]).toBeLessThan(found[3] ?? 0);
    expect(await countEdits(page, (await pageIds(page))[0] ?? '')).toBe(1);
    await expect(page.getByTestId('editor-auto')).toBeEnabled();
    await snap(page, 'margins-box-dragged');
  });

  await test.step('dragging a side of the border sets that margin for the page alone', async () => {
    // The step keeps the margins a book starts with, which are lengths of the paper in millimetres, so the border lies at
    // the same place on a scan of any size, and the canvas is fitted to hold it
    for (const [name, millimetres] of MARGIN_FIELDS) {
      await expect(
        page.getByTestId('step-panel-settings').getByRole('spinbutton', { name, exact: true }),
      ).toHaveValue(millimetres);
    }
    await expect
      .poll(async () => (await pairOf(layer, 'data-side-left')).x, { timeout: RUN_TIMEOUT_MS })
      .toBeGreaterThan(0);
    // Every side of the border can be grabbed: its handle stands inside the canvas and above the toolbar that floats over
    // the bottom of it, the places of the handles being counted from the corner of the layer
    const view = await layer.boundingBox();
    expect(view).not.toBeNull();
    for (const side of SIDES) {
      const handle = await pairOf(layer, `data-side-${side}`);
      expect(handle.x).toBeGreaterThan(0);
      expect(handle.x).toBeLessThan(view?.width ?? 0);
      expect(handle.y).toBeGreaterThan(0);
      expect(handle.y).toBeLessThan((view?.height ?? 0) - TOOLBAR_PX);
    }
    await snap(page, 'margins-border-in-view');
    const before = await numbersOf(layer, 'data-outer');
    const side = await pairOf(layer, 'data-side-left');
    await dragFrom(page, layer, side, { x: -DRAG_PX, y: 0 });
    await expect.poll(() => settingSaves.length, { timeout: RUN_TIMEOUT_MS }).toBe(1);
    expect(LEFT_MARGIN_SETTINGS.some((name) => settingSaves[0]?.endsWith(`/${name}`))).toBe(true);
    await settled();
    const after = await numbersOf(layer, 'data-outer');
    expect(after[0]).toBeLessThan(before[0] ?? 0);
    const [first = '', second = ''] = await pageIds(page);
    expect((await readSettings(page, first)).length).toBe(1);
    expect(await readSettings(page, second)).toHaveLength(0);
    await snap(page, 'margins-side-dragged');
  });

  await test.step('the alignment is chosen in the panel and kept for the page', async () => {
    await page.getByTestId('align-vertical').getByLabel('Bottom').click();
    await expect.poll(() => settingSaves.length, { timeout: RUN_TIMEOUT_MS }).toBe(2);
    await settled();
    const [first = ''] = await pageIds(page);
    const own = await readSettings(page, first);
    expect(own.some((params) => params.align_vertical === ('bottom' as unknown))).toBe(true);
    await expect(page.getByTestId('align-vertical').getByLabel('Bottom')).toBeChecked();
  });

  await test.step('a run on all pages gives every page one size without measuring the book, and keeps the work on the page', async () => {
    await runAll(page);
    await waitForIdleJobs(page, projectId);
    const sizes = await sizesOf(page, projectId);
    expect(sizes).toHaveLength(PAGES);
    expect(new Set(sizes.map((size) => `${size.width}x${size.height}`)).size).toBe(1);
    const [first = ''] = await pageIds(page);
    expect(await countEdits(page, first)).toBe(1);
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    expect(await numbersOf(layer, 'data-rect')).toEqual(boxByHand);
    await snap(page, 'margins-after-run-on-all-pages');
  });

  await test.step('Auto takes the box away and the step finds it again, where the settings of the page stay', async () => {
    await page.getByTestId('editor-auto').click();
    await expect(layer).toHaveAttribute('data-figure', 'found', { timeout: RUN_TIMEOUT_MS });
    await settled();
    // The box is drawn from the result again once the page has been read after the run, which the busy mark does not wait for
    await expect
      .poll(
        async () => {
          const again = await numbersOf(layer, 'data-rect');
          return Math.max(...again.map((value, index) => Math.abs(value - (found[index] ?? 0))));
        },
        { timeout: RUN_TIMEOUT_MS },
      )
      .toBeLessThanOrEqual(FOUND_TOLERANCE_PX);
    const [first = ''] = await pageIds(page);
    expect(await countEdits(page, first)).toBe(0);
    expect((await readSettings(page, first)).length).toBe(1);
    await snap(page, 'margins-after-auto');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
