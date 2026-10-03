import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Locator, type Page, test } from '@playwright/test';
import {
  createBook,
  registerAndSignIn,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * The workspace of a book in a narrow window: the canvas takes the whole width, the strip and the panel open as sheets
 * from the two buttons above it, picking a page closes the strip, the stage bar shows the open stage and lists the
 * others in a menu, and widening the window brings back the three panels with the widths they had before.
 */

const PAGES = 6;
const NARROW = { width: 800, height: 1000 };
const WIDE = { width: 1280, height: 1000 };
const DRAG_PX = 60;
const DRAG_STEPS = 6;
// A width measured twice may differ by the rounding of a fraction of a pixel
const WIDTH_TOLERANCE_PX = 1;

/**
 * Take a screenshot once the sheets and menus have finished sliding or fading in, so it shows them at rest.
 *
 * An animation that is cancelled, such as the fade of a menu that closes meanwhile, rejects `finished` with an
 * `AbortError`. It is at rest too, so the rejection settles the wait like a finish does.
 */
async function snapAtRest(page: Page, name: string): Promise<void> {
  await page.evaluate(() =>
    Promise.allSettled(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity)
        .map((animation) => animation.finished),
    ),
  );
  await snap(page, name);
}

async function widthOf(locator: Locator): Promise<number> {
  const box = await locator.boundingBox();
  return box?.width ?? 0;
}

test('a reader works in a narrow window and gets the wide layout back', async ({ page }) => {
  const folder = await writePagesFolder(PAGES);
  const caption = page.getByTestId('canvas-caption');
  const strip = page.getByTestId('strip');
  const panel = page.getByTestId('panel');
  const bar = page.getByTestId('stage-bar');
  let bookPath = '';
  let stripWidth = 0;
  let panelWidth = 0;

  await test.step('a book with pages opens on the Geometry stage in a wide window', async () => {
    await page.setViewportSize(WIDE);
    await registerAndSignIn(page);
    await createBook(page, 'A book in a narrow window');
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await uploadFolder(page, folder, PAGES);
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
  });

  await test.step('the reader widens the strip, so the widths differ from the defaults', async () => {
    const before = await widthOf(strip);
    const handle = await page.getByRole('separator').first().boundingBox();
    expect(handle).not.toBeNull();
    const x = (handle?.x ?? 0) + (handle?.width ?? 0) / 2;
    const y = (handle?.y ?? 0) + (handle?.height ?? 0) / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + DRAG_PX, y, { steps: DRAG_STEPS });
    await page.mouse.up();
    await expect.poll(() => widthOf(strip)).toBeGreaterThan(before);
    // The widths are written to the browser a moment after the last move
    await expect
      .poll(() => page.evaluate(() => Object.keys(localStorage).join(' ')))
      .toContain('bookreviver.workspace');
    stripWidth = await widthOf(strip);
    panelWidth = await widthOf(panel);
  });

  await test.step('below 1024 px the canvas has the whole width and the bar shows one stage', async () => {
    await page.setViewportSize(NARROW);
    await expect(strip).toHaveCount(0);
    await expect(page.getByRole('separator')).toHaveCount(0);
    const canvasBox = await page.getByTestId('viewer-canvas').boundingBox();
    expect(canvasBox?.width ?? 0).toBeGreaterThan(NARROW.width - 2 * 16);

    await expect(bar.getByTestId('stage-geometry')).toHaveAttribute('aria-current', 'page');
    await expect(bar.getByTestId('stage-geometry')).toContainText('Geometry');
    await expect(bar.getByTestId('stage-cleanup')).toHaveCount(0);
    await snap(page, 'narrow-canvas');
  });

  await test.step('the strip opens as a sheet and a picked page closes it', async () => {
    await page.getByTestId('toggle-strip').click();
    const sheet = page.getByTestId('strip-sheet');
    await expect(sheet).toBeVisible();
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await snapAtRest(page, 'narrow-strip-sheet');

    await page.getByTestId('strip-page').nth(2).click();
    await expect(sheet).toHaveCount(0);
    await expect(page).toHaveURL(/page=/);
    await expect(caption).toContainText('3 of 6');
  });

  await test.step('the panel opens as a sheet from the right and closes on Escape', async () => {
    await page.getByTestId('toggle-panel').click();
    const sheet = page.getByTestId('panel-sheet');
    await expect(sheet).toBeVisible();
    await expect(sheet.getByTestId('stage-title')).toHaveText('Geometry');
    await snapAtRest(page, 'narrow-panel-sheet');

    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
  });

  await test.step('another stage is one pick away in the menu', async () => {
    await bar.getByTestId('stage-menu').click();
    await expect(page.getByTestId('stage-menu-list')).toBeVisible();
    await snapAtRest(page, 'narrow-stage-menu');

    await page.getByTestId('stage-menu-list').getByTestId('stage-cleanup').click();
    await expect(page).toHaveURL(/\/stages\/cleanup/);
    await expect(page.getByTestId('stage-screen')).toHaveAttribute('data-stage', 'cleanup');
    await expect(bar.getByTestId('stage-cleanup')).toHaveAttribute('aria-current', 'page');
  });

  await test.step('the Order stage has the same layout without a strip', async () => {
    await page.getByTestId('stage-menu').click();
    await page.getByTestId('stage-menu-list').getByTestId('stage-page-order').click();
    await expect(page.getByTestId('order-grid')).toBeVisible();
    await expect(page.getByTestId('toggle-strip')).toHaveCount(0);
    await page.getByTestId('toggle-panel').click();
    await expect(page.getByTestId('panel-sheet')).toBeVisible();
    await page.keyboard.press('Escape');
  });

  await test.step('at 1280 px the three panels return with their earlier widths', async () => {
    await page.getByTestId('stage-menu').click();
    await page.getByTestId('stage-menu-list').getByTestId('stage-geometry').click();
    await expect(page.getByTestId('stage-screen')).toHaveAttribute('data-stage', 'geometry');
    await page.setViewportSize(WIDE);
    await expect(strip).toBeVisible();
    await expect(panel).toBeVisible();
    await expect(page.getByTestId('canvas')).toBeVisible();
    await expect(page.getByTestId('stage-geometry')).toBeVisible();
    await expect.poll(() => widthOf(strip)).toBeGreaterThan(stripWidth - WIDTH_TOLERANCE_PX);
    expect(Math.abs((await widthOf(strip)) - stripWidth)).toBeLessThanOrEqual(WIDTH_TOLERANCE_PX);
    expect(Math.abs((await widthOf(panel)) - panelWidth)).toBeLessThanOrEqual(WIDTH_TOLERANCE_PX);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
