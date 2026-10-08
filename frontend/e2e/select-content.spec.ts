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
  writeSheetsFolder,
} from './support/account';
import { dragFrom, numbersOf, pairOf } from './support/layer';
import { finishedRuns, pageIds } from './support/page-work';

/**
 * The borders of the content on the Select content step: they are only placed. The step records the frame of the content
 * and leaves the page as it is, so a border that is dragged moves a box over a picture that does not change, and the canvas
 * never loads again. The page is cut by the borders on the Margins step, which places the box the reader set.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const DRAG_PX = 30;
// How far the box a step records may stand from the box on the canvas, in pixels of the picture
const BOX_TOLERANCE_PX = 2;
// How long the canvas has to stay as it is to count as at rest, and how many times that is waited for
const QUIET_MS = 2_000;
const QUIET_ROUNDS = 8;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;

/** What the canvas goes through while the reader works, from the moment the observer is put in. */
declare global {
  interface Window {
    canvasStates: string[];
  }
}

/** A version of the Geometry stage as the server holds it, with what the scenario reads of it. */
interface StoredVersion {
  id: string;
  input_id: string | null;
  processor: { key: string };
  data: { frame?: Rect; content_box?: Rect; width_px: number; height_px: number };
  transform: { kind: string };
}

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Read the versions that made the current result of the Geometry stage of the page, the first step first. */
async function currentChain(
  page: Page,
  projectId: string,
  pageId: string,
): Promise<StoredVersion[]> {
  const base = `/api/v1/projects/${projectId}/pages/${pageId}`;
  const stages = (await (await page.request.get(`${base}/stages`)).json()) as {
    items: { stage: string; head_version_id: string }[];
  };
  const listed = await page.request.get(`${base}/versions?stage=geometry&scale=full&size=100`);
  const byId = new Map(
    ((await listed.json()) as { items: StoredVersion[] }).items.map((version) => [
      version.id,
      version,
    ]),
  );
  const chain: StoredVersion[] = [];
  let next = byId.get(
    stages.items.find((item) => item.stage === 'geometry')?.head_version_id ?? '',
  );
  while (next !== undefined) {
    chain.unshift(next);
    next = byId.get(next.input_id ?? '');
  }
  return chain;
}

/**
 * Start writing what the canvas goes through, which the scenario reads later: every value its `data-state` takes, and
 * `layer-gone` and `layer-back` for each time the editor is taken off the page and put on it again.
 */
async function watchCanvas(page: Page): Promise<void> {
  await page.evaluate(() => {
    const canvas = document.querySelector('[data-testid="viewer-canvas"]');
    const log = [canvas?.getAttribute('data-state') ?? 'missing'];
    window.canvasStates = log;
    if (canvas !== null) {
      new MutationObserver(() => {
        log.push(canvas.getAttribute('data-state') ?? 'missing');
        console.log(
          'DBGSTATE',
          location.pathname.slice(10, 16),
          Date.now() % 100000,
          canvas.getAttribute('data-state'),
          (canvas.getAttribute('data-sources') ?? '').split('/').slice(-3).join('/'),
        );
      }).observe(canvas, { attributes: true, attributeFilter: ['data-state', 'data-sources'] });
    }
    let present = document.querySelector('[data-testid="editor-layer"]') !== null;
    new MutationObserver(() => {
      const now = document.querySelector('[data-testid="editor-layer"]') !== null;
      if (now !== present) {
        log.push(now ? 'layer-back' : 'layer-gone');
        present = now;
      }
    }).observe(document.body, { childList: true, subtree: true });
  });
}

/**
 * Wait until the canvas has had nothing to load and the editor has stayed on the page for a while, since the versions and the
 * rows of a run arrive one after another and the picture may change when the last of them does.
 */
async function waitForQuietCanvas(page: Page): Promise<void> {
  await watchCanvas(page);
  for (let round = 0; round < QUIET_ROUNDS; round += 1) {
    await page.waitForTimeout(QUIET_MS);
    const log = await page.evaluate(() => window.canvasStates);
    if (log.length === 1) {
      return;
    }
    await watchCanvas(page);
  }
  throw new Error('The canvas did not settle.');
}

// Tall enough for the pictures of the key states to show the bar, the canvas and the panel
test.use({ viewport: { width: 1280, height: 1000 } });

test('a border of the content is dragged on Select content without the picture loading again, and Margins places that box', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  page.on('console', (m) => {
    if (m.text().startsWith('DBG')) console.log(m.text().slice(0, 400));
  });
  page.on('framenavigated', (f) =>
    console.log(
      'DBGNAV',
      f.url().slice(32, 38),
      Date.now() % 100000,
      f
        .url()
        .replace(/^.*stages/, '')
        .slice(0, 60),
    ),
  );
  const folder = await writeSheetsFolder(1);
  const layer = page.getByTestId('editor-layer');
  const canvas = page.getByTestId('viewer-canvas');
  let projectId = '';
  let found: number[] = [];
  let set: number[] = [];
  const saves: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/geometry/')) {
      saves.push(request.url());
    }
  });
  // An edit starts a run of the stage on the page, and the next change waits until that run is over
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0, { timeout: RUN_TIMEOUT_MS });
    await waitForIdleJobs(page, projectId);
  };

  await test.step('the stage runs on a scan and Select content shows the borders it found', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of borders');
    await uploadFolder(page, folder, 1);
    projectId = openProjectId(page);
    await waitForIdleJobs(page, projectId);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(1);
    await page.getByTestId('bar-step').filter({ hasText: 'Select content' }).click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(layer).toHaveAttribute('aria-label', 'Frame of the content');
    // "Auto on all pages" runs the stage, so the step has found the frame on the page
    const before = await finishedRuns(page);
    await page.getByTestId('step-auto').click();
    await expect.poll(() => finishedRuns(page), { timeout: RUN_TIMEOUT_MS }).toBe(before + 1);
    await waitForIdleJobs(page, projectId);
    await expect(layer).not.toHaveAttribute('data-figure', 'by-hand');
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await waitForQuietCanvas(page);
    found = await numbersOf(layer, 'data-rect');
    expect(found[2]).toBeGreaterThan(0);
    await snap(page, 'select-content-found');
  });

  await test.step('the step cut nothing: its page is the page it read, with the frame recorded', async () => {
    const [id = ''] = await pageIds(page);
    const chain = await currentChain(page, projectId, id);
    const place = chain.findIndex((version) => version.processor.key === 'geometry.crop');
    const crop = chain[place];
    const read = chain[place - 1];
    expect(crop?.transform.kind).toBe('identity');
    expect(crop?.data.width_px).toBe(read?.data.width_px);
    expect(crop?.data.height_px).toBe(read?.data.height_px);
    expect(crop?.data.frame).toBeDefined();
  });

  await test.step('the first border set by hand is saved, and the stage runs by the recipe that is shown', async () => {
    // The page the stage ran on was sent to the recipe of its kind, and an edit runs the one that is shown, so the first
    // edit may change what the step reads. The next ones are the ones the reader works with
    const bottom = await pairOf(layer, 'data-handle-bottom');
    await dragFrom(page, layer, bottom, { x: 0, y: -DRAG_PX });
    await expect(layer).toHaveAttribute('data-figure', 'by-hand', { timeout: RUN_TIMEOUT_MS });
    await settled();
    await expect(layer).toBeVisible();
    await waitForQuietCanvas(page);
    found = await numbersOf(layer, 'data-rect');
  });

  await test.step('dragging a side moves the box and the picture under it stays loaded', async () => {
    const side = await pairOf(layer, 'data-handle-right');
    await dragFrom(page, layer, side, { x: -DRAG_PX, y: 0 });
    await expect(layer).not.toHaveAttribute('data-rect', found.join(','));
    set = await numbersOf(layer, 'data-rect');
    expect(set[2]).toBeLessThan(found[2] ?? 0);
    await expect.poll(() => saves.length, { timeout: RUN_TIMEOUT_MS }).toBe(2);
    await settled();
    // The save made new versions, and the canvas neither loaded its picture again nor took the editor off the page
    await expect(layer).toBeVisible();
    expect(await page.evaluate(() => window.canvasStates)).toEqual(['ready']);
    expect(await numbersOf(layer, 'data-rect')).toEqual(set);
    await expect(layer).toHaveAttribute('data-figure', 'by-hand');
    await snap(page, 'select-content-dragged');
  });

  await test.step('the result of the step records the box of the reader, in the pixels of the page it kept', async () => {
    const [id = ''] = await pageIds(page);
    const chain = await currentChain(page, projectId, id);
    const crop = chain.find((version) => version.processor.key === 'geometry.crop');
    const [left = 0, top = 0, width = 0, height = 0] = set;
    const recorded = crop?.data.frame;
    expect(recorded?.left).toBeCloseTo(left, 0);
    expect(recorded?.top).toBeCloseTo(top, 0);
    expect(recorded?.width).toBeCloseTo(width, 0);
    expect(recorded?.height).toBeCloseTo(height, 0);
  });

  await test.step('Margins places the box the reader set, and cuts the block out of the whole page', async () => {
    await page.getByTestId('bar-step').filter({ hasText: 'Margins' }).click();
    await expect(layer).toHaveAttribute('aria-label', 'Content box and margins of the page');
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    const placed = await numbersOf(layer, 'data-rect');
    for (const [index, value] of placed.entries()) {
      expect(Math.abs(value - (set[index] ?? 0))).toBeLessThanOrEqual(BOX_TOLERANCE_PX);
    }
    const [id = ''] = await pageIds(page);
    const margins = (await currentChain(page, projectId, id)).find(
      (version) => version.processor.key === 'geometry.normalize',
    );
    expect(Math.abs((margins?.data.content_box?.width ?? 0) - (set[2] ?? 0))).toBeLessThanOrEqual(
      BOX_TOLERANCE_PX,
    );
    await snap(page, 'select-content-placed-by-margins');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
