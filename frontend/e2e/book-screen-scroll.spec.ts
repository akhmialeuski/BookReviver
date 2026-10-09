import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Locator, type Page, test } from '@playwright/test';
import {
  createBook,
  markPagesAsText,
  openProjectId,
  registerAndSignIn,
  snap,
  stepIdsOf,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';
import { openTimeline, pageIds, putSetting } from './support/page-work';

/**
 * The book screen never scrolls as a whole: the header and the stage bar stay in the window on every stage, and only
 * the strip, the canvas and the panel scroll inside it. Scrolling each of them to its end with the wheel and bringing the
 * last element of the panel into view moves neither the document nor the frame that holds the screen of the stage.
 * The panel of a stage is never wider than its column: at 1280 px, and at the narrowest width the divider allows, nothing
 * in it reaches past its right edge and its scrolling area has nothing to scroll sideways.
 */

test.use({ viewport: { width: 1280, height: 720 } });

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 180_000;
const DESKEW = 'geometry.deskew';
const DESKEW_TITLE = 'Deskew';
// Enough changes of the first page for its history to make the panel taller than the window
const SLANTS_OF_THE_PAGE = [1, 2, 3, 4];
const WHEEL_PX = 5_000;
const STAGE_STEPS = [
  ['geometry', 'Deskew'],
  ['cleanup', 'Binarization'],
] as const;
// The default panel is a quarter of the window, and the narrowest the divider keeps is well under it
const NARROWEST_PANEL_PX = 250;
const DRAG_STEPS = 10;
// Short of the right edge by less than the smallest panel and by more than half of it
const OVERSHOOT_PX = 150;
// A width measured in fractions of a pixel may differ from the one the box was laid out with
const SUBPIXEL_PX = 1;

/** How far the window and the frame of the stage screen can scroll, and what pokes out below the window. */
interface ScrollState {
  documentTop: number;
  documentOverflow: number;
  frameTop: number;
  frameOverflow: number;
  escaped: string[];
}

/** Scroll each part under the pointer to its end with the wheel, then bring the last element of the panel into view. */
async function scrollEveryPart(page: Page, parts: readonly Locator[]): Promise<void> {
  for (const part of parts) {
    if ((await part.count()) === 0) {
      continue;
    }
    await part.first().hover();
    await page.mouse.wheel(0, WHEEL_PX);
  }
  await page
    .getByTestId('stage-panel-scroll')
    .locator(':scope > :last-child')
    .evaluate((last) => last.scrollIntoView({ block: 'end' }));
}

/** Read how far the window and the frame of the stage screen can scroll and which elements stick out below the window. */
function readScrollState(page: Page): Promise<ScrollState> {
  return page.evaluate(() => {
    const root = document.scrollingElement ?? document.documentElement;
    const frame = document.querySelector('[data-testid="book-screen"]');
    if (frame === null) {
      throw new Error('The book screen has no frame for its stage.');
    }
    // Elements that only the window could scroll to, which is what makes the whole screen scroll
    const escaped = [...document.querySelectorAll<HTMLElement>('body *')]
      .filter(
        (element) =>
          element.getBoundingClientRect().bottom > window.innerHeight &&
          getComputedStyle(element).position === 'absolute' &&
          (element.offsetParent === null || element.offsetParent === document.body),
      )
      .map((element) => `${element.tagName.toLowerCase()}.${element.className}`);
    return {
      documentTop: root.scrollTop,
      documentOverflow: root.scrollHeight - window.innerHeight,
      frameTop: frame.scrollTop,
      frameOverflow: frame.scrollHeight - frame.clientHeight,
      escaped,
    };
  });
}

/** Check that neither the window nor the frame of the stage screen scrolled, and that the header and the bar show. */
async function expectScreenInPlace(page: Page): Promise<void> {
  const state = await readScrollState(page);
  expect(state, JSON.stringify(state)).toMatchObject({
    documentTop: 0,
    frameTop: 0,
    escaped: [],
  });
  expect(state.documentOverflow, JSON.stringify(state)).toBeLessThanOrEqual(0);
  expect(state.frameOverflow, JSON.stringify(state)).toBeLessThanOrEqual(0);
  await expect(page.getByTestId('book-title')).toBeInViewport();
  await expect(page.getByTestId('stage-bar')).toBeInViewport();
}

test('the book screen stays in place on Import, Order and Geometry while its strip, canvas and panel scroll to the end', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const parts = [
    page.getByTestId('page-strip'),
    page.getByTestId('order-grid'),
    page.getByTestId('stage-panel-scroll'),
  ];
  let bookPath = '';

  await test.step('a book of three pages is imported, and its first page has a history at Deskew', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book that keeps its header');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    const [pageId = ''] = await pageIds(page);
    const [stepId = ''] = await stepIdsOf(page, 'geometry', DESKEW);
    for (const slant of SLANTS_OF_THE_PAGE) {
      await putSetting(page, pageId, stepId, slant);
    }
  });

  await test.step('the Import stage stays in place', async () => {
    await scrollEveryPart(page, parts);
    await expectScreenInPlace(page);
  });

  await test.step('the Order stage stays in place', async () => {
    await page.goto(`${bookPath}/stages/page-order`);
    await expect(page.getByTestId('order-grid')).toBeVisible();
    await scrollEveryPart(page, parts);
    await expectScreenInPlace(page);
  });

  await test.step('the Geometry stage stays in place on the step it opens on', async () => {
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
    await scrollEveryPart(page, parts);
    await expectScreenInPlace(page);
  });

  await test.step('the Geometry stage stays in place with a step and the history of the page open', async () => {
    await page.getByTestId('bar-step').filter({ hasText: DESKEW_TITLE }).click();
    await expect(page.getByTestId('panel-step')).toBeVisible();
    await openTimeline(page);
    await scrollEveryPart(page, parts);
    await expectScreenInPlace(page);
    await snap(page, 'book-screen-geometry-scrolled');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

/** How wide the panel is against what it holds, and which of its elements reach past its right edge. */
interface PanelOverflow {
  panelWidth: number;
  /** The widest content the panel has to show, taken over the frame and its scrolling area. */
  contentWidth: number;
  /** The visible elements that reach past the right edge of the box they stand in, with the pixels they stick out by. */
  wider: string[];
}

/**
 * Measure the panel of the stage: its width, the width its content needs, and the elements that reach past the right
 * edge of the part of the panel they stand in (the header, the scrolling area or the footer) once its padding is taken
 * off. An element inside a box that clips or scrolls its own content, such as a scrolling table, is left out, because
 * the box answers for it.
 */
function measurePanel(page: Page): Promise<PanelOverflow> {
  return page.getByTestId('stage-panel').evaluate((panel) => {
    const wider: string[] = [];
    for (const part of panel.children) {
      const style = getComputedStyle(part);
      const edge =
        part.getBoundingClientRect().right -
        Number.parseFloat(style.paddingRight) -
        Number.parseFloat(style.borderRightWidth);
      const clips = (element: Element): boolean => {
        for (let ancestor = element.parentElement; ancestor && ancestor !== part; ) {
          if (getComputedStyle(ancestor).overflowX !== 'visible') {
            return true;
          }
          ancestor = ancestor.parentElement;
        }
        return false;
      };
      for (const element of part.querySelectorAll<HTMLElement>('*')) {
        const box = element.getBoundingClientRect();
        if (box.width > 0 && box.right > edge + 1 && !clips(element)) {
          const text = (element.textContent ?? '').trim().slice(0, 40);
          const testId = element.dataset.testid ?? '';
          wider.push(
            `${element.tagName.toLowerCase()}[${testId}] +${Math.round(box.right - edge)}px "${text}"`,
          );
        }
      }
    }
    const scroll = panel.querySelector<HTMLElement>('[data-testid="stage-panel-scroll"]');
    return {
      panelWidth: panel.clientWidth,
      contentWidth: Math.max(panel.scrollWidth, scroll?.scrollWidth ?? 0),
      wider,
    };
  });
}

/** Check that nothing in the panel is wider than its part of the panel, and that its scrolling area has nothing to scroll sideways. */
async function expectPanelFits(page: Page): Promise<void> {
  const measured = await measurePanel(page);
  const shown = JSON.stringify(measured, null, 1);
  expect(measured, shown).toMatchObject({ wider: [] });
  expect(measured.contentWidth, shown).toBeLessThanOrEqual(measured.panelWidth + SUBPIXEL_PX);
}

/**
 * Drag the handle of the panel towards the right edge of the window, which leaves the panel as narrow as the divider
 * allows: a drag past the smallest width stops at it, and only a drag past half of it shuts the panel.
 */
async function narrowPanel(page: Page): Promise<number> {
  const handle = await page.getByRole('separator').last().boundingBox();
  const viewport = page.viewportSize();
  if (handle === null || viewport === null) {
    throw new Error('The handle of the panel or the window has no size.');
  }
  const y = handle.y + handle.height / 2;
  await page.mouse.move(handle.x + handle.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(viewport.width - OVERSHOOT_PX, y, { steps: DRAG_STEPS });
  await page.mouse.up();
  return (await page.getByTestId('stage-panel').boundingBox())?.width ?? 0;
}

test('the panel of a stage is never wider than its column at 1280 px, nor at its narrowest', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  let bookPath = '';

  await test.step('a book of three pages of text is imported', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with a panel that fits');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
  });

  /** Open the stage, check its panel with no step open and with the step open, as the width of the panel is now. */
  const checkStages = async (width: string): Promise<void> => {
    for (const [stage, step] of STAGE_STEPS) {
      await test.step(`the panel of ${stage} fits ${width}, with no step open and with ${step} open`, async () => {
        await page.goto(`${bookPath}/stages/${stage}`);
        await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
        await expect(page.getByTestId('stage-panel')).toBeVisible();
        await expectPanelFits(page);
        await page.getByTestId('bar-step').filter({ hasText: step }).click();
        await expect(page.getByTestId('panel-step')).toBeVisible();
        await expectPanelFits(page);
        await snap(page, `panel-${stage}-${width.replaceAll(' ', '-')}`);
      });
    }
  };

  await checkStages('at its default width');

  await test.step('the reader drags the panel to the narrowest width the divider allows', async () => {
    expect(await narrowPanel(page)).toBeLessThan(NARROWEST_PANEL_PX);
  });

  await checkStages('at its narrowest');

  await rm(path.dirname(folder), { recursive: true, force: true });
});
