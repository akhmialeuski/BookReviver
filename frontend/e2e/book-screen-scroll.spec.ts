import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Locator, type Page, test } from '@playwright/test';
import {
  createBook,
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
 */

const PAGES = 3;
const SCENARIO_TIMEOUT_MS = 180_000;
const DESKEW = 'geometry.deskew';
const DESKEW_TITLE = 'Deskew';
// Enough changes of the first page for its history to make the panel taller than the window
const SLANTS_OF_THE_PAGE = [1, 2, 3, 4];
const WHEEL_PX = 5_000;

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
    await expect(page.getByTestId('step-panel')).toBeVisible();
    await openTimeline(page);
    await scrollEveryPart(page, parts);
    await expectScreenInPlace(page);
    await snap(page, 'book-screen-geometry-scrolled');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
