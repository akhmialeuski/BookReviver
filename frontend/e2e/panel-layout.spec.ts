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
  writePagesFolder,
} from './support/account';
import { runPages } from './support/page-work';

/**
 * The panel of every stage is laid out by one component, so Geometry and Cleanup, with a step open, show their parts
 * in one order: the recipe, the open step, its settings in one bordered frame, the section of the page with the facts
 * of the page last, and the history. The step is named by its title alone, and "This page" comes once.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const STAGE_STEPS = [
  ['geometry', 'Deskew'],
  ['cleanup', 'Binarization'],
] as const;
// The parts of the panel in the order the layout gives them, by their test ids
const PANEL_ORDER = ['panel-recipe', 'panel-step', 'panel-settings', 'panel-page', 'page-history'];

/** Read the test ids of the parts of the scrolling area of the panel, in the order they stand. */
async function partsOf(page: Page): Promise<string[]> {
  return page
    .getByTestId('stage-panel-scroll')
    .evaluate((area) =>
      [...area.children].map((child) => (child as HTMLElement).dataset.testid ?? ''),
    );
}

test('Geometry and Cleanup lay out their panels in one order, with the facts of the page right above the history', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  let bookPath = '';

  await test.step('a book of two pages of text is run through Geometry and Cleanup', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with one panel');
    await uploadFolder(page, folder, PAGES);
    await waitForIdleJobs(page, openProjectId(page));
    await markPagesAsText(page);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    for (const [stage] of STAGE_STEPS) {
      await page.goto(`${bookPath}/stages/${stage}`);
      await expect(page.getByTestId('page-strip').getByTestId('strip-page')).toHaveCount(PAGES);
      await runPages(page);
    }
  });

  for (const [stage, step] of STAGE_STEPS) {
    await test.step(`the panel of ${stage} with ${step} open stands in the order of the layout`, async () => {
      await page.goto(`${bookPath}/stages/${stage}`);
      await page.getByTestId('bar-step').filter({ hasText: step }).click();
      await page.getByTestId('strip-page').first().click();
      const panel = page.getByTestId('stage-panel');
      await expect(panel.getByTestId('panel-facts')).toBeVisible();

      expect(await partsOf(page)).toEqual(PANEL_ORDER);
      // The step is named by its title alone, and the settings stand in the frame the layout draws
      await expect(panel.getByTestId('step-panel-title')).toHaveText(step);
      expect(
        await panel
          .getByTestId('panel-settings')
          .evaluate((frame) => Number.parseFloat(getComputedStyle(frame).borderTopWidth)),
      ).toBeGreaterThan(0);
      // The facts of the page are the last part of its section, so they stand right above the history
      expect(
        await panel
          .getByTestId('panel-page')
          .evaluate((section) => (section.lastElementChild as HTMLElement | null)?.dataset.testid),
      ).toBe('panel-facts');
      await expect(panel.getByText(/^This page/)).toHaveCount(1);
      await expect(panel).not.toContainText('Settings of the step');
      await snap(page, `panel-layout-${stage}`);
    });
  }

  await rm(path.dirname(folder), { recursive: true, force: true });
});
