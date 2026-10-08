import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openProjectId,
  registerAndSignIn,
  setKind,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writeSheetsFolder,
} from './support/account';
import { dragFrom } from './support/layer';
import { runPages } from './support/page-work';

/**
 * The Cleanup stage on scans of a sheet of paper: the default recipe of a new book has four steps in the bar and one
 * recipe for each kind of page, the form of the binarization shows the fields of the method that is chosen, a run on all
 * pages processes the plate by the recipe of the pictures, the picture zones and the brush of the eraser are set by hand on one page, and the
 * erased page is what the stage stands on.
 */

const PAGES = 3;
const SCAN_SCALE = 2;
const DUST_SPECKS = 6;
const PLATE_POSITION = 2;
const STEP_TITLES = ['Binarization', 'Despeckle', 'Thickness', 'Fill zones'];
const BINARIZE_INDEX = 0;
const DESPECKLE_INDEX = 1;
const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 120_000;
const BRUSH_FROM = { x: 120, y: 150 };
const BRUSH_BY = { x: 160, y: 40 };
const METHODS = ['Otsu', 'Sauvola', 'Wolf', 'ISauvola', 'Su', 'Gatos', 'NICK', 'Bradley'];

const ERASER_INDEX = 3;
const ZONES = 'Picture zones';

// Tall enough for the pictures of the key states to show the form and the page together
test.use({ viewport: { width: 1280, height: 1000 } });

/** The versions a stage made on a page, as the API lists them. */
interface ListedVersion {
  processor: { key: string };
  edit_hash: string;
  scale: string;
  state: string;
  data: Record<string, unknown>;
  images: { full: string } | null;
}

/** A manual edit as the API lists it. */
interface StoredEdit {
  step_id: string;
  kind: string;
  geometry: { strokes?: unknown[] } | null;
  mask: string | null;
}

/** Read the identifier of the page that is selected in the strip. */
async function currentPageId(page: Page): Promise<string> {
  const id = await page
    .locator('[data-testid="strip-page"][aria-pressed="true"]')
    .first()
    .getAttribute('data-page-id');
  expect(id).not.toBeNull();
  return id ?? '';
}

/** Read the full versions of the open page that a step of a stage made. */
async function versionsOf(page: Page, pageId: string, processor: string): Promise<ListedVersion[]> {
  const listed = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/versions?stage=cleanup&size=100`,
  );
  const items = ((await listed.json()) as { items: ListedVersion[] }).items;
  return items.filter(
    (version) =>
      version.processor.key === processor && version.scale === 'full' && version.state === 'ready',
  );
}

test('the Cleanup stage binarizes, despeckles and erases, with a recipe for plates and the editors set by hand', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  // The scans are large and carry specks of dust, so the despeckling has something to remove at its own scale
  const folder = await writeSheetsFolder(PAGES, { scale: SCAN_SCALE, dust: DUST_SPECKS });
  const layer = page.getByTestId('editor-layer');
  const strip = page.getByTestId('strip-page');
  const marks = page.getByTestId('strip-content');
  const barSteps = page.getByTestId('bar-step');
  const settled = async (): Promise<void> => {
    await expect(page.getByTestId('editor-busy')).toHaveCount(0);
    await waitForIdleJobs(page, openProjectId(page));
  };
  const runAll = async (): Promise<void> => {
    await runPages(page);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
  };
  const saves: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/cleanup/')) {
      saves.push(request.url().replace(/^.*\/edits\/cleanup\//, ''));
    }
  });
  let bookPath = '';

  await test.step('three sheets, one of them a plate, are cut to their frames by the Geometry stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to clean');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    // The sheets are pages of text but the plate, which the reader says is one
    await markPagesAsText(page, [PLATE_POSITION]);
    await setKind(page, PLATE_POSITION, 'plate');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(strip).toHaveCount(PAGES);
    await runAll();
  });

  await test.step('the Cleanup stage starts with four steps in the order binarize, despeckle, thickness, eraser', async () => {
    await page.goto(`${bookPath}/stages/cleanup`);
    await expect(page.getByTestId('stage-title')).toHaveText('Cleanup');
    await expect(strip).toHaveCount(PAGES);
    await expect(page.getByTestId('recipe-select')).toContainText('Text');
    // The steps are in the bar only, and the panel of the recipe lists none
    await expect(page.getByTestId('recipe-steps')).toHaveCount(0);
    await expect(barSteps).toHaveCount(STEP_TITLES.length);
    for (const [index, title] of STEP_TITLES.entries()) {
      await expect(barSteps.nth(index)).toContainText(title);
    }
    const kinds = await page.getByTestId('recipe-select').locator('option').allTextContents();
    // The options name the kinds of page and say how many pages of the book are of each
    expect(kinds.map((name) => name.split(' · ')[0]?.trim()).sort()).toEqual([
      'Black-and-white picture',
      'Blank page',
      'Colour picture',
      'Text',
    ]);
  });

  await test.step('the form of the binarization offers the methods and shows only the fields of the one that is chosen', async () => {
    await barSteps.nth(BINARIZE_INDEX).click();
    await expect(page.getByTestId('step-panel-title')).toHaveText('Binarization');
    await expect(page.getByRole('slider', { name: 'Window, px' })).toBeVisible();
    await expect(page.getByRole('slider', { name: 'Coefficient k' })).toBeVisible();
    await expect(page.getByTestId('stage-panel')).not.toContainText('output_dpi');
    const method = page
      .getByTestId('panel-settings')
      .locator('button[aria-haspopup="listbox"]')
      .first();
    await expect(method).toHaveText('Sauvola');
    await page.getByRole('slider', { name: 'Coefficient k' }).scrollIntoViewIfNeeded();
    await snap(page, 'cleanup-method-form');
    await method.click();
    const offered = await page.getByRole('listbox').getByRole('option').allTextContents();
    expect(offered.map((name) => name.trim())).toEqual(METHODS);
    await page.getByRole('option', { name: 'Su', exact: true }).click();
    await expect(page.getByRole('slider', { name: 'Window, px' })).toBeVisible();
    await expect(page.getByRole('slider', { name: 'Coefficient k' })).toHaveCount(0);
    await method.click();
    await page.getByRole('option', { name: 'Otsu', exact: true }).click();
    await expect(page.getByRole('slider', { name: 'Window, px' })).toHaveCount(0);
    await expect(page.getByRole('slider', { name: 'Stroke thickness' })).toBeVisible();
    await snap(page, 'cleanup-method-form-otsu');
    await method.click();
    await page.getByRole('option', { name: 'Sauvola', exact: true }).click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
  });

  await test.step('the despeckling is made stronger in the recipe, which the dust on the scans needs', async () => {
    await barSteps.nth(DESPECKLE_INDEX).click();
    const strength = page.getByTestId('panel-settings').getByRole('spinbutton', {
      name: 'Strength',
    });
    await expect(strength).toHaveValue('2');
    await strength.fill('3');
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
  });

  await test.step('a run on all pages processes the plate by the recipe of the pictures and the text pages by the recipe of text', async () => {
    await runAll();
    await expect(marks).toHaveCount(PAGES, { timeout: RUN_TIMEOUT_MS });
    await expect(page.locator('[data-testid="strip-content"][data-content="text"]')).toHaveCount(
      PAGES - 1,
    );
    await expect(
      page.locator('[data-testid="strip-content"][data-content="bw-picture"]'),
    ).toHaveCount(1);
    await expect(page.getByTestId('recipe-select')).toContainText(`Text · ${PAGES - 1} pages`);
    await expect(page.getByTestId('recipe-select')).toContainText(
      'Black-and-white picture · 1 page',
    );
    await strip.first().click();
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(page.getByTestId('this-page-facts')).toContainText('Sauvola', {
      timeout: RUN_TIMEOUT_MS,
    });
    await snap(page, 'cleanup-binarized');
  });

  await test.step('the despeckling wrote the mask of what it removed, which the page of text has', async () => {
    const pageId = await currentPageId(page);
    const [made] = await versionsOf(page, pageId, 'cleanup.despeckle');
    expect(made?.data.specks).toBeGreaterThan(0);
    const maskPath = made?.images?.full.replace(/full\.\w+$/, 'mask.png') ?? '';
    const mask = await page.request.get(maskPath);
    expect(mask.ok()).toBe(true);
    expect(mask.headers()['content-type']).toContain('image/png');
    const maskPage = await page.context().newPage();
    await maskPage.goto(maskPath);
    await snap(maskPage, 'cleanup-despeckle-mask');
    await maskPage.close();
  });

  await test.step('the picture zones are added by hand, and Auto takes them away', async () => {
    // The shape of an open step is on the page from the start, so the step of the zones is opened and not a button pressed
    await barSteps.nth(BINARIZE_INDEX).click();
    await expect(layer).toBeVisible();
    await expect(layer).toHaveAttribute('aria-label', ZONES);
    await expect(layer).toHaveAttribute('data-zones', '0');
    await page.getByTestId('regions-add').click();
    await expect(layer).toHaveAttribute('data-zones', '1');
    await expect(page.getByTestId('regions-list')).toContainText('Picture 1');
    await expect.poll(() => saves.length).toBe(1);
    expect(saves[0]).toBe((await stepIdsOf(page, 'cleanup', 'cleanup.binarize'))[0]);
    await settled();
    await page.getByTestId('canvas-auto').click();
    await expect(layer).toHaveAttribute('data-zones', '0', { timeout: RUN_TIMEOUT_MS });
    await settled();
  });

  await test.step('the brush paints a stroke over the page, which is saved with its mask and runs the stage again', async () => {
    await barSteps.nth(ERASER_INDEX).click();
    await expect(layer).toHaveAttribute('aria-label', 'Eraser brush');
    await expect(layer).toHaveAttribute('data-strokes', '0');
    await expect(page.getByTestId('brush-size')).toContainText('% of the page width');
    await snap(page, 'cleanup-despeckled');
    const [eraserStep] = await stepIdsOf(page, 'cleanup', 'cleanup.eraser');
    const putBody = page.waitForRequest(
      (request) =>
        request.method() === 'PUT' && request.url().includes(`/edits/cleanup/${eraserStep}`),
    );
    await dragFrom(page, layer, BRUSH_FROM, BRUSH_BY);
    await expect(layer).toHaveAttribute('data-strokes', '1');
    const request = await putBody;
    expect(request.headers()['content-type']).toContain('multipart/form-data');
    await expect(barSteps.nth(ERASER_INDEX)).toHaveAttribute('data-state', 'by-hand', {
      timeout: RUN_TIMEOUT_MS,
    });
    await settled();
    const [eraser] = await stepIdsOf(page, 'cleanup', 'cleanup.eraser');
    expect(saves.at(-1)).toBe(eraser);
    // The server kept the strokes beside the mask the browser painted from them
    const listed = await page.request.get(
      `/api/v1/projects/${openProjectId(page)}/pages/${await currentPageId(page)}/edits/cleanup`,
    );
    const edits = ((await listed.json()) as { items: StoredEdit[] }).items;
    const stored = edits.find((edit) => edit.step_id === eraser);
    expect(stored?.kind).toBe('brush-mask');
    expect(stored?.geometry?.strokes).toHaveLength(1);
    const mask = await page.request.get(stored?.mask ?? '');
    expect(mask.headers()['content-type']).toContain('image/png');
    expect((await mask.body()).length).toBeGreaterThan(0);
  });

  await test.step('the erased page is what the stage stands on, and the eraser step is no longer left as it was', async () => {
    const pageId = await currentPageId(page);
    await expect
      .poll(
        async () =>
          (await versionsOf(page, pageId, 'cleanup.eraser')).some((v) => v.edit_hash !== ''),
        {
          timeout: RUN_TIMEOUT_MS,
        },
      )
      .toBe(true);
    const erased = (await versionsOf(page, pageId, 'cleanup.eraser')).find(
      (version) => version.edit_hash !== '',
    );
    expect(erased?.data.skipped).toBe(false);
    // The tiles of the erased picture are fetched after the viewer opens it
    const tile = page.waitForResponse(
      (response) => response.url().includes('default.') && response.ok(),
    );
    // The shape of the step gives way to the compare, which draws the picture the step read beside the picture it made
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-swipe').click();
    await expect(layer).toHaveCount(0);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await tile;
    await snap(page, 'cleanup-erased');
  });

  await test.step('a run on all pages keeps the stroke of the reader and the kinds of the pages', async () => {
    await runAll();
    await expect(
      page.locator('[data-testid="strip-content"][data-content="bw-picture"]'),
    ).toHaveCount(1);
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-off').click();
    await expect(barSteps.nth(ERASER_INDEX)).toHaveAttribute('data-state', 'by-hand');
    await expect(layer).toHaveAttribute('data-strokes', '1');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
