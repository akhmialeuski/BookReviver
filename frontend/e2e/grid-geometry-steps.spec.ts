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
  writeSheetsFolder,
} from './support/account';
import { test } from './support/fixtures';
import { dragFrom, pairOf } from './support/layer';
import { runPages } from './support/page-work';

/**
 * The grid over the page on every step of Geometry and on the result of the stage.
 *
 * The grid is one choice of the book. It is off until the reader makes it, and once it is on, it is shown on Perspective,
 * Deskew, Dewarp, Select content and Margins and on the result of the stage, stands above the editor of the step, and
 * takes no pointer, so a handle under a line of the grid is still dragged. The key G and the button switch it on any of
 * them, and the choice outlives a reload.
 */

const SCENARIO_TIMEOUT_MS = 300_000;
const RUN_TIMEOUT_MS = 90_000;
const PAGES = 1;
const STEP_ADDRESS = /\/stages\/geometry\/steps\/[0-9a-f-]{36}(\?|$)/;
// Outward, into the margin of the scan, so that the corner stays inside the picture
const CORNER_DRAG = { x: -12, y: -9 };

// The steps of the default recipe in the order of the bar, with the name their picture is saved under
const STEPS = [
  { title: 'Perspective', picture: 'perspective' },
  { title: 'Deskew', picture: 'deskew' },
  { title: 'Dewarp', picture: 'dewarp' },
  { title: 'Select content', picture: 'select-content' },
  { title: 'Margins', picture: 'margins' },
] as const;

// Tall enough for the pictures to show the bar, the canvas and the panel
test.use({ viewport: { width: 1280, height: 1000 } });

test('the grid is off until chosen, then shown over the editor of every Geometry step and over the result of the stage', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeSheetsFolder(PAGES);
  const layer = page.getByTestId('editor-layer');
  const grid = page.getByTestId('canvas-grid');
  const gridButton = page.getByTestId('canvas-grid-toggle');
  const canvas = page.getByTestId('viewer-canvas');
  const stepOf = (title: string) => page.getByTestId('bar-step').filter({ hasText: title });
  let bookPath = '';

  await test.step('a book of one sheet opens on Geometry, and the stage has run on it', async () => {
    await openLibrary(page);
    await createBook(page, 'A book with a grid');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('bar-step')).toHaveCount(STEPS.length);
    await runPages(page);
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
  });

  await test.step('with no choice made the grid is off on every step', async () => {
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(gridButton).toHaveAttribute('aria-pressed', 'false');
    await expect(grid).toHaveCount(0);
    for (const { title } of STEPS) {
      await stepOf(title).click();
      await expect(page).toHaveURL(STEP_ADDRESS);
      await expect(layer).toBeVisible();
      await expect(gridButton).toHaveAttribute('aria-pressed', 'false');
      await expect(grid).toHaveCount(0);
    }
  });

  await test.step('the key G switches the grid on at the step that is open', async () => {
    await page.keyboard.press('g');
    await expect(grid).toBeVisible();
    await expect(gridButton).toHaveAttribute('aria-pressed', 'true');
  });

  await test.step('the grid is shown on every step, above the editor and taking no pointer', async () => {
    for (const { title, picture } of STEPS) {
      await stepOf(title).click();
      await expect(page).toHaveURL(STEP_ADDRESS);
      await expect(canvas).toHaveAttribute('data-state', 'ready');
      await expect(layer).toBeVisible();
      await expect(grid).toBeVisible();
      await expect(gridButton).toHaveAttribute('aria-pressed', 'true');

      // The grid covers the editor, takes no pointer, and stands above it in the stacking order of the same parent
      const covers = await grid.evaluate(
        (element, editor) => {
          const own = getComputedStyle(element);
          const box = element.getBoundingClientRect();
          const under = editor?.getBoundingClientRect();
          return {
            pointerEvents: own.pointerEvents,
            zIndex: Number(own.zIndex),
            sameParent: editor !== null && element.parentElement?.contains(editor) === true,
            covered:
              under !== undefined &&
              box.left <= under.left &&
              box.top <= under.top &&
              box.right >= under.right &&
              box.bottom >= under.bottom,
          };
        },
        await layer.elementHandle(),
      );
      expect(covers).toEqual({
        pointerEvents: 'none',
        zIndex: 10,
        sameParent: true,
        covered: true,
      });
      await snap(page, `grid-${picture}`);
    }
  });

  await test.step('a corner under the grid is still dragged', async () => {
    await stepOf('Perspective').click();
    await expect(layer).toHaveAttribute('aria-label', 'Corners of the sheet');
    await expect(grid).toBeVisible();
    const corner = await pairOf(layer, 'data-corner-top-left');
    await dragFrom(page, layer, await pairOf(layer, 'data-handle-top-left'), CORNER_DRAG);
    await expect(layer).not.toHaveAttribute('data-corner-top-left', `${corner.x},${corner.y}`);
    await expect(grid).toBeVisible();
    await waitForIdleJobs(page, openProjectId(page));
  });

  await test.step('the button switches the grid off, and it stays off on another step and after a reload', async () => {
    await gridButton.click();
    await expect(grid).toHaveCount(0);
    await expect(gridButton).toHaveAttribute('aria-pressed', 'false');
    await stepOf('Select content').click();
    await expect(page).toHaveURL(STEP_ADDRESS);
    await expect(layer).toBeVisible();
    await expect(grid).toHaveCount(0);
    await expect(gridButton).toHaveAttribute('aria-pressed', 'false');

    await page.reload();
    await expect(layer).toBeVisible();
    await expect(grid).toHaveCount(0);
  });

  await test.step('the key G switches it on again at a step, and the choice is kept through a reload', async () => {
    await page.keyboard.press('g');
    await expect(grid).toBeVisible();

    await page.reload();
    await expect(layer).toBeVisible();
    await expect(grid).toBeVisible();
    await expect(gridButton).toHaveAttribute('aria-pressed', 'true');
  });

  await test.step('the grid is shown over the result of the stage too, with the editor shut, and the key G switches it off there', async () => {
    // The address of a stage with a step bar always opens a step, so the result is the picture the compare draws with
    // the editor shut
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-swipe').click();
    await expect(layer).toHaveCount(0);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(grid).toBeVisible();
    await expect(gridButton).toHaveAttribute('aria-pressed', 'true');
    await snap(page, 'grid-stage-result');

    await page.keyboard.press('g');
    await expect(grid).toHaveCount(0);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
