import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  createBook,
  registerAndSignIn,
  stepIdsOf,
  uploadFolder,
  writePagesFolder,
  writeScansFolder,
} from './support/account';
import { dragFrom, pairOf } from './support/layer';

/**
 * The page editors on the canvas: the rotation handles of the Geometry stage with its field, its wheel, its undo and its
 * "Auto", and the split line of the Split stage with its arrow keys, its handles and the same undo and "Auto", for a
 * book on the automatic split and for a book on the older recipe that cuts every spread.
 *
 * A save is followed by a run of the stage on the one page, so each step waits for the panel to show the result of that
 * run: the method `By hand` and the number the edit gave. The pages are solid colours, so the step finds nothing on its
 * own, and anything it reports after a save comes from the edit.
 */

const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;
const PAGES = 2;
const WIDE_SCANS = 2;
const NUDGES = 3;
const NUDGE_SHIFT_PX = 10;
const DRAG_PX = 40;
const WHEEL_NOTCH = 100;

test('a reader turns a page by hand with the handle, the field and the wheel, takes it back and goes to Auto', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const layer = page.getByTestId('editor-layer');
  const facts = page.getByTestId('this-page-facts');
  const angle = page.getByRole('textbox', { name: 'Angle in degrees' });
  const saves: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/geometry/')) {
      saves.push(request.url());
    }
  });

  await test.step('a page opens on the Geometry stage with the editor shut', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to turn');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(layer).toHaveCount(0);
  });

  await test.step('the editors of the sheet and the frame start from what their step found, so the stage runs first', async () => {
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    // The sheet, the angle, the curves, the frame and the block on the page
    await expect(page.getByTestId('editor-step')).toHaveCount(5);
    await expect(page.getByTestId('editor-auto')).toBeDisabled();
  });

  await test.step('picking the angle opens its editor on the page, with the handle, and compare gives way to it', async () => {
    await page.getByTestId('editor-step').filter({ hasText: 'Angle' }).click();
    await expect(page.getByRole('button', { name: 'Set by hand' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(layer).toBeVisible();
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(layer).toHaveAttribute('data-degrees', '0');
    await expect(page.getByTestId('compare-menu')).toBeDisabled();
  });

  await test.step('an angle typed in the field is saved and the page is turned by it, with the method By hand', async () => {
    await angle.fill('2.5');
    await angle.press('Enter');
    await expect(layer).toHaveAttribute('data-degrees', '2.5');
    await expect(facts).toContainText('2.5°', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('By hand');
    expect(saves).toHaveLength(1);
    await expect(page.getByTestId('editor-auto')).toBeEnabled();
  });

  await test.step('Alt and the wheel change the angle by a tenth, and a run of notches is saved once', async () => {
    await layer.hover();
    await page.keyboard.down('Alt');
    await page.mouse.wheel(0, -WHEEL_NOTCH);
    await page.mouse.wheel(0, -WHEEL_NOTCH);
    await page.keyboard.up('Alt');
    await expect(layer).toHaveAttribute('data-degrees', '2.7');
    await expect(facts).toContainText('2.7°', { timeout: RUN_TIMEOUT_MS });
    expect(saves).toHaveLength(2);
  });

  await test.step('dragging the right handle of the axis up turns the page and saves when it is let go', async () => {
    const handle = await pairOf(layer, 'data-handle-rotation');
    await dragFrom(page, layer, handle, { x: 0, y: -DRAG_PX });
    const turned = Number(await layer.getAttribute('data-degrees'));
    expect(turned).toBeGreaterThan(2.7);
    await expect(facts).toContainText(`${turned.toFixed(1)}°`, { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('Ctrl+Z puts back the angle before the drag', async () => {
    await layer.focus();
    await page.keyboard.press('Control+z');
    await expect(layer).toHaveAttribute('data-degrees', '2.7');
    await expect(facts).toContainText('2.7°', { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('Auto deletes the edit and the step finds the result by itself again', async () => {
    await page.getByTestId('editor-auto').click();
    await expect(facts).toContainText('Automatic', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).not.toContainText('By hand');
    await expect(page.getByTestId('editor-auto')).toBeDisabled();
  });

  await test.step('Ctrl+Z after Auto brings the angle back', async () => {
    await layer.focus();
    await page.keyboard.press('Control+z');
    await expect(facts).toContainText('By hand', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('2.7°');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('a reader moves the split line of the automatic split with the keys and the handles, and the halves are cut by it', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScansFolder(WIDE_SCANS);
  const total = WIDE_SCANS + 1;
  const strip = page.getByTestId('strip-page');
  const layer = page.getByTestId('editor-layer');
  const facts = page.getByTestId('this-page-facts');
  const saves: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/page-split/')) {
      saves.push(request.url());
    }
  });
  let found = 0;

  await test.step('the wide scans are cut, and the first one opens with its line over the scan', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of spreads');
    await uploadFolder(page, folder, total);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/page-split`);
    // The import queues the automatic split by itself, so the book has its pages without a press
    await expect(strip).toHaveCount(WIDE_SCANS * 2 + 1, { timeout: RUN_TIMEOUT_MS });
    await page.getByTestId('strip-filter-wide').click();
    await strip.first().click();
    await expect(layer).toBeVisible();
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(facts).toContainText('Automatic');
    found = (await pairOf(layer, 'data-line-start')).x;
  });

  await test.step('the halves are labelled above the scan with the pages they become', async () => {
    await expect(page.getByTestId('line-label-left')).toContainText('Left · becomes');
    await expect(page.getByTestId('line-label-right')).toContainText('Right · becomes');
    const label = await page.getByTestId('line-label-left').boundingBox();
    const area = await layer.boundingBox();
    const scanTop = Number(await layer.getAttribute('data-scan-top'));
    expect(label).not.toBeNull();
    expect(area).not.toBeNull();
    // The label ends where the scan begins, and does not lie on it
    expect((label?.y ?? 0) + (label?.height ?? 0)).toBeLessThanOrEqual((area?.y ?? 0) + scanTop);
  });

  await test.step('Shift and an arrow move the line by ten pixels of the scan, and a run of keys is saved once', async () => {
    await layer.focus();
    for (let press = 0; press < NUDGES; press += 1) {
      await page.keyboard.press('Shift+ArrowRight');
    }
    const moved = found + NUDGES * NUDGE_SHIFT_PX;
    await expect(layer).toHaveAttribute('data-line-start', new RegExp(`^${moved},`));
    await expect(facts).toContainText(`${moved} px`, { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('By hand');
    expect(saves).toHaveLength(1);
  });

  await test.step('a plain arrow moves the line by one pixel', async () => {
    await page.keyboard.press('ArrowLeft');
    const moved = found + NUDGES * NUDGE_SHIFT_PX - 1;
    await expect(layer).toHaveAttribute('data-line-start', new RegExp(`^${moved},`));
    await expect(facts).toContainText(`${moved} px`, { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('dragging the top end moves the cut and saves when it is let go', async () => {
    const before = await layer.getAttribute('data-line-start');
    const end = await pairOf(layer, 'data-handle-start');
    await dragFrom(page, layer, end, { x: DRAG_PX, y: 0 });
    await expect(layer).not.toHaveAttribute('data-line-start', before ?? '');
    const dragged = (await pairOf(layer, 'data-line-start')).x;
    expect(dragged).toBeGreaterThan(found + NUDGES * NUDGE_SHIFT_PX - 1);
    expect(saves.length).toBeGreaterThanOrEqual(3);
  });

  await test.step('Ctrl+Z puts back the line before the drag', async () => {
    await layer.focus();
    await page.keyboard.press('Control+z');
    const back = found + NUDGES * NUDGE_SHIFT_PX - 1;
    await expect(layer).toHaveAttribute('data-line-start', new RegExp(`^${back},`));
    await expect(facts).toContainText(`${back} px`, { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('a run on all pages keeps the line, so the halves are still cut by it', async () => {
    const back = found + NUDGES * NUDGE_SHIFT_PX - 1;
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    // The Wide filter lists the two halves of each wide scan, and a run does not change that
    await expect(strip).toHaveCount(WIDE_SCANS * 2);
    await expect(layer).toHaveAttribute('data-line-start', new RegExp(`^${back},`));
    await expect(facts).toContainText(`${back} px`);
    await expect(facts).toContainText('By hand');
  });

  await test.step('Auto deletes the line and the cut goes back to the one the step found', async () => {
    await page.getByTestId('editor-auto').click();
    await expect(facts).toContainText('Automatic', { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText(`${found} px`);
    await expect(page.getByTestId('editor-auto')).toBeDisabled();
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('a book on the older recipe that cuts every spread keeps its split line editor', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScansFolder(WIDE_SCANS);
  const total = WIDE_SCANS + 1;
  const strip = page.getByTestId('strip-page');
  const layer = page.getByTestId('editor-layer');
  const facts = page.getByTestId('this-page-facts');
  const saved: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/edits/page-split/')) {
      saved.push(request.url());
    }
  });
  let found = 0;

  await test.step('the book is cut by the import, and then takes the recipe that cuts every spread', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book on the older recipe');
    await uploadFolder(page, folder, total);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/page-split`);
    await expect(strip).toHaveCount(WIDE_SCANS * 2 + 1, { timeout: RUN_TIMEOUT_MS });

    const spread = await page
      .getByTestId('recipe-select')
      .locator('option', { hasText: /^Spread/ })
      .getAttribute('value');
    expect(spread).not.toBeNull();
    await page.getByTestId('recipe-select').selectOption(spread ?? '');
    await page.getByTestId('recipe-use').click();
    await expect(page.getByTestId('recipe-active')).toBeVisible();
    await expect(page.getByTestId('recipe-step')).toContainText('Spread');
  });

  await test.step('the line is moved and saved for the recipe, and the halves are cut by it', async () => {
    await page.getByTestId('strip-filter-wide').click();
    await strip.first().click();
    await expect(layer).toBeVisible();
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    found = (await pairOf(layer, 'data-line-start')).x;

    await layer.focus();
    await page.keyboard.press('Shift+ArrowRight');
    const moved = found + NUDGE_SHIFT_PX;
    await expect(layer).toHaveAttribute('data-line-start', new RegExp(`^${moved},`));
    await expect(facts).toContainText(`${moved} px`, { timeout: RUN_TIMEOUT_MS });
    await expect(facts).toContainText('By hand');
    expect(saved).toHaveLength(1);
    const [spreadStep] = await stepIdsOf(page, 'page-split', 'split.spread');
    expect(saved[0]).toContain(`/edits/page-split/${spreadStep}`);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
