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
  writeBentSheetsFolder,
} from './support/account';
import { dragFrom, pairOf } from './support/layer';

/**
 * The Geometry stage on scans of a sheet whose lines are bent into the gutter: the dewarping step of the default recipe
 * flattens each page along the curves of its lines, and the reader corrects the two curves of one page on the canvas.
 *
 * The page is shown bent before the stage runs, flattened after it, and under the curves editor with its top and bottom
 * curve. A node dragged by the reader is saved when it is let go, the stage runs again on that page, and the curves survive
 * a run of the stage on all pages until "Auto" takes them away.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const PAGES = 2;
const BEND_PX = 18;
const NODE_DRAG_PX = 10;
const CURVES = 'Page curves';
const CURVE_NODES = '5';
const GRID_ROWS = '5';
const SIMPLE_ROWS = '2';

// Tall enough for the whole page to lie above the toolbar of the canvas, so both curves can be reached
test.use({ viewport: { width: 1280, height: 1000 } });

/** Run the stage on all pages and wait until every page is up to date. */
async function runAll(page: Page): Promise<void> {
  await page.getByTestId('run-menu').click();
  await page.getByTestId('run-all').click();
  await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
    timeout: RUN_TIMEOUT_MS,
  });
}

/** What the API holds of a ready version of the dewarping step of the first page of the open book. */
interface DewarpVersion {
  transform: { kind: string; mesh_key: string | null };
  data: Record<string, unknown>;
  edit_hash: string;
}

/** Read the newest ready version of the dewarping step of the first page of the open book. */
async function dewarpVersion(page: Page): Promise<DewarpVersion> {
  const projectId = openProjectId(page);
  const pages = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
  const [first] = ((await pages.json()) as { items: { id: string }[] }).items;
  if (first === undefined) {
    throw new Error('The book has no page.');
  }
  const listed = await page.request.get(
    `/api/v1/projects/${projectId}/pages/${first.id}/versions?stage=geometry&scale=full&size=100`,
  );
  const items = (
    (await listed.json()) as {
      items: (DewarpVersion & {
        processor: { key: string };
        state: string;
        created_at: string;
      })[];
    }
  ).items;
  const made = items
    .filter((item) => item.processor.key === 'geometry.dewarp' && item.state === 'ready')
    .toSorted((a, b) => b.created_at.localeCompare(a.created_at))[0];
  if (made === undefined) {
    throw new Error('The first page has no ready version of the dewarping.');
  }
  return made;
}

test('a reader flattens a page bent into the gutter, lays its two curves by hand, and the curves outlive a run on all pages', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeBentSheetsFolder(PAGES, BEND_PX);
  const layer = page.getByTestId('editor-layer');
  const facts = page.getByTestId('this-page-facts');
  const steps = page.getByTestId('editor-step');
  const curvesStep = steps.filter({ hasText: CURVES });
  const moreControl = page.getByTestId('mesh-more-control');
  // An edit starts a run of the stage on the page, and the next change waits until that run is over
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0);
    await waitForIdleJobs(page, openProjectId(page));
  };
  const factsText = async (): Promise<string> => (await facts.textContent()) ?? '';
  const saves: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/geometry/')) {
      saves.push(request.url().replace(/^.*\/edits\/geometry\//, ''));
    }
  });
  let automaticFacts = '';
  let node = { x: 0, y: 0 };

  await test.step('the pages are bent before the stage runs, and the recipe has a dewarping step after the deskew', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book bent at the gutter');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(page.getByTestId('bar-step')).toHaveCount(5);
    await expect(page.getByTestId('bar-step').nth(2)).toContainText('Dewarp');
    await snap(page, 'dewarp-bent-page');
  });

  await test.step('the stage flattens the page along the curves of its lines and stores the mesh of the version', async () => {
    await runAll(page);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(facts).toContainText('Bend of the lines', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('Lines followed');
    automaticFacts = await factsText();
    const version = await dewarpVersion(page);
    expect(version.transform.kind).toBe('mesh');
    expect(version.transform.mesh_key).toMatch(/mesh\.json$/);
    expect(Number(version.data.bend)).toBeGreaterThan(2);
    expect(Number(version.data.lines)).toBeGreaterThan(5);
    expect(version.data.mesh).toBeDefined();
    expect(version.edit_hash).toBe('');
    await expect(page.getByTestId('this-page-review')).toHaveCount(0);
    await snap(page, 'dewarp-flattened-page');
  });

  await test.step('the curves editor shows the top curve and the bottom curve of five nodes each, over the page the step read', async () => {
    await page.getByRole('button', { name: 'Set by hand' }).click();
    await curvesStep.click();
    await expect(layer).toBeVisible();
    await expect(layer).toHaveAttribute('aria-label', 'Curves of the lines');
    await expect(curvesStep).toHaveAttribute('aria-pressed', 'true');
    await expect(layer).toHaveAttribute('data-rows', SIMPLE_ROWS);
    await expect(layer).toHaveAttribute('data-columns', CURVE_NODES);
    await expect(page.getByTestId('mesh-hint')).toBeVisible();
    // The curves lie inside the page and the bottom one is below the top one
    const top = await pairOf(layer, 'data-node-0-2');
    node = await pairOf(layer, 'data-node-1-2');
    expect(top.y).toBeGreaterThan(0);
    expect(node.y).toBeGreaterThan(top.y);
    await snap(page, 'dewarp-curves-editor');
  });

  await test.step('More control shows the whole grid of nodes, and Fewer controls goes back to the two curves', async () => {
    await moreControl.click();
    await expect(moreControl).toHaveAttribute('aria-pressed', 'true');
    await expect(layer).toHaveAttribute('data-rows', GRID_ROWS);
    await expect(moreControl).toContainText('Fewer controls');
    await moreControl.click();
    await expect(layer).toHaveAttribute('data-rows', SIMPLE_ROWS);
    await expect(moreControl).toContainText('More control');
  });

  await test.step('dragging a node saves the curves of the user and flattens the page again by them', async () => {
    const handle = await pairOf(layer, 'data-handle-1-2');
    await dragFrom(page, layer, handle, { x: 0, y: NODE_DRAG_PX });
    await expect(layer).not.toHaveAttribute('data-node-1-2', `${node.x},${node.y}`);
    await expect(curvesStep).toHaveAttribute('data-manual', 'true', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('By hand', { timeout: RUN_TIMEOUT_MS });
    await settled();
    const [dewarp = ''] = await stepIdsOf(page, 'geometry', 'geometry.dewarp');
    expect(saves).toEqual([dewarp]);
    // The page was flattened again by the curves of the user, so its bend is not the one the step found
    await expect.poll(factsText, { timeout: RUN_TIMEOUT_MS }).not.toBe(automaticFacts);
    expect((await dewarpVersion(page)).edit_hash).not.toBe('');
    node = await pairOf(layer, 'data-node-1-2');
  });

  await test.step('a node of the grid is dragged too, and Ctrl+Z takes the move back', async () => {
    await moreControl.click();
    await expect(layer).toHaveAttribute('data-rows', GRID_ROWS);
    const handle = await pairOf(layer, 'data-handle-2-2');
    const before = await pairOf(layer, 'data-node-2-2');
    await dragFrom(page, layer, handle, { x: 0, y: NODE_DRAG_PX });
    await expect(layer).not.toHaveAttribute('data-node-2-2', `${before.x},${before.y}`);
    await settled();
    const [dewarp = ''] = await stepIdsOf(page, 'geometry', 'geometry.dewarp');
    expect(saves).toEqual([dewarp, dewarp]);

    await layer.focus();
    await page.keyboard.press('Control+z');
    await settled();
    await moreControl.click();
    await expect(layer).toHaveAttribute('data-rows', SIMPLE_ROWS);
    await expect(layer).toHaveAttribute('data-node-1-2', `${node.x},${node.y}`);
  });

  await test.step('a run on all pages keeps the curves of the user on the page', async () => {
    await runAll(page);
    // The sheet, the angle, the curves, the frame and the block on the page
    await expect(steps).toHaveCount(5, { timeout: RUN_TIMEOUT_MS });
    await expect(curvesStep).toHaveAttribute('data-manual', 'true');
    await expect(layer).toHaveAttribute('data-node-1-2', `${node.x},${node.y}`);
    expect((await dewarpVersion(page)).edit_hash).not.toBe('');
  });

  await test.step('Auto takes the curves away and the step finds them by itself again', async () => {
    await page.getByTestId('editor-auto').click();
    await expect(curvesStep).toHaveAttribute('data-manual', 'false', { timeout: RUN_TIMEOUT_MS });
    await settled();
    await expect(page.getByTestId('editor-auto')).toBeDisabled();
    // The version of the step without the edit is found again in the cache, so it is not the newest, and the facts of
    // the page are the ones the step found before the reader moved a node
    await expect.poll(factsText, { timeout: RUN_TIMEOUT_MS }).toBe(automaticFacts);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
