import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  createBook,
  registerAndSignIn,
  uploadFolder,
  writePagesFolder,
  writeScansFolder,
} from './support/account';

/**
 * The processing workspace on the Geometry stage: the recipe drawn from the schema of its processor, a preview of the
 * open page that is asked for once and not again, the compare of the page before and after, a run on all pages, the
 * Check filter with the reason under each page, and the choice of an earlier result of a page. Then the Split stage:
 * the book that is split by its import, the doubtful cuts under Check, and the choice of one page or two that a run on all
 * pages keeps.
 *
 * The pages are solid colours, which have no lines of text to level, so the step leaves each one as it was and marks it
 * for a look, which is what the Check filter is for.
 */

const PAGES = 4;
const SCENARIO_TIMEOUT_MS = 240_000;
const RUN_TIMEOUT_MS = 90_000;

test('a reader previews, runs and checks the Geometry stage', async ({ page }) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const previews: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/stages/geometry/preview')) {
      previews.push(request.url());
    }
  });
  let bookPath = '';

  await test.step('a book with pages opens on the Geometry stage', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to straighten');
    await uploadFolder(page, folder, PAGES);
    bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
  });

  await test.step('the recipe is drawn from the processor and its schema', async () => {
    await expect(page.getByTestId('recipe-select')).toContainText('Automatic');
    await expect(page.getByTestId('recipe-active')).toBeVisible();
    // A new book is straightened in three steps, the sheet first and the frame of the content last
    await expect(page.getByTestId('recipe-step')).toHaveCount(3);
    await expect(page.getByTestId('recipe-step').nth(0)).toContainText('1 · Perspective');
    await expect(page.getByTestId('recipe-step').nth(1)).toContainText('2 · Deskew');
    await expect(page.getByTestId('recipe-step').nth(2)).toContainText('3 · Crop');
    // The settings of the first step are open, with the titles of the schema and no name of the code
    await expect(page.getByRole('slider', { name: 'Smallest sheet' })).toBeVisible();
    await expect(page.getByTestId('stage-panel')).not.toContainText('min_sheet_fraction');
    // The steps that are planned stand under the real ones
    await expect(page.getByTestId('coming-steps')).toContainText('Dewarp by mesh');
    await expect(page.getByTestId('coming-steps')).toContainText('Soon');
  });

  await test.step('the settings of the last step are opened, and a value outside its limits cannot be saved', async () => {
    await page.getByRole('button', { name: 'Show the settings of the Crop step' }).click();
    const margin = page.getByRole('spinbutton', { name: 'Margin' });
    await expect(margin).toHaveValue('8');
    await expect(page.getByTestId('stage-panel')).not.toContainText('margin_percent');
    await margin.fill('99');
    await expect(margin).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByTestId('recipe-save')).toBeDisabled();
    await expect(page.getByTestId('preview-toggle')).toBeDisabled();
    await margin.fill('8');
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
  });

  await test.step('a preview is asked for once and not again for what is shown', async () => {
    await page.getByTestId('preview-toggle').click();
    // The picture after is the half of the compare that the preview fills
    await expect(page).toHaveURL(/compare=swipe/);
    await expect(page.getByTestId('compare-handle')).toBeVisible();
    await expect(page.getByText('After · Geometry preview')).toBeVisible({
      timeout: RUN_TIMEOUT_MS,
    });
    expect(previews).toHaveLength(1);

    const margin = page.getByRole('spinbutton', { name: 'Margin' });
    await margin.fill('12');
    await expect(page.getByTestId('preview-working')).toBeVisible();
    await expect(page.getByTestId('preview-working')).toBeHidden({ timeout: RUN_TIMEOUT_MS });
    expect(previews).toHaveLength(2);

    // Back to the settings that were shown first: the preview made for them is shown again
    await margin.fill('8');
    await expect(page.getByTestId('preview-working')).toBeHidden();
    await page.waitForTimeout(1_000);
    expect(previews).toHaveLength(2);
    await page.getByTestId('preview-toggle').click();
  });

  await test.step('the page before and after is compared by a swipe, side by side and with a key held', async () => {
    await expect(canvas).toHaveAttribute('data-mode', 'swipe');
    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-side').click();
    await expect(page).toHaveURL(/compare=side/);
    await expect(page.getByTestId('viewer-canvas-after')).toBeVisible();
    await expect(page.getByTestId('compare-handle')).toHaveCount(0);

    await page.getByTestId('compare-menu').click();
    await page.getByTestId('compare-swipe').click();
    await expect(page.getByTestId('viewer-canvas-after')).toBeHidden();
    await page.getByTestId('compare-handle').focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByTestId('compare-handle')).toHaveAttribute('aria-valuenow', '55');

    // Space shows the page before for as long as it is held
    await page.mouse.click(400, 400);
    await page.keyboard.down('Space');
    await expect(canvas).toHaveAttribute('data-holding', 'true');
    await page.keyboard.up('Space');
    await expect(canvas).toHaveAttribute('data-holding', 'false');
  });

  await test.step('a run on all pages goes over every page and the strip follows it', async () => {
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
    // Every page has a result now, and the step was unsure of each
    await page.getByTestId('strip-filter-check').click();
    await expect(page).toHaveURL(/filter=check/);
    await expect(page.getByTestId('strip-page')).toHaveCount(PAGES, { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('the Check filter says why under each page and offers the way out on the page', async () => {
    await expect(page.getByTestId('strip-reason').first()).toContainText('Left as it was');
    await expect(page.getByTestId('strip-reason')).toHaveCount(PAGES);
    await page.getByTestId('strip-page').first().click();
    await expect(page.getByTestId('this-page-review')).toContainText('Left as it was');
    await expect(page.getByRole('button', { name: 'Set by hand' })).toBeEnabled();
    await expect(page.getByTestId('this-page-facts')).toContainText('Confidence');
  });

  await test.step('a changed recipe says how many pages it makes out of date and is saved by the button', async () => {
    await page.getByRole('spinbutton', { name: 'Margin' }).fill('10');
    await expect(page.getByTestId('recipe-stale-warning')).toContainText(
      `${PAGES} pages out of date`,
    );
    await page.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
    await expect(page.getByTestId('stale-banner')).toContainText(
      'Order changed after these pages were straightened',
    );
    await expect(page.getByTestId('stale-banner-run')).toContainText(`Run again on ${PAGES} pages`);
  });

  await test.step('the stale pages are run again and the page keeps both of its results', async () => {
    await page.getByTestId('stale-banner-run').click();
    await expect(page.getByTestId('stale-banner')).toHaveCount(0, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByTestId('history-entry')).toHaveCount(2, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByTestId('history-entry').first()).toHaveAttribute('data-current', 'true');
  });

  await test.step('an earlier result is chosen from the history and becomes the current one', async () => {
    const entries = page.getByTestId('history-entry');
    await expect(entries.nth(1)).toHaveAttribute('data-current', 'false');
    await entries.nth(1).getByTestId('history-use').click();
    await expect(entries.nth(1)).toHaveAttribute('data-current', 'true', {
      timeout: RUN_TIMEOUT_MS,
    });
    await expect(entries.first()).toHaveAttribute('data-current', 'false');
    await expect(entries.nth(1)).toContainText('Margin 8');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

const WIDE_SCANS = 3;

test('an imported folder of spreads and single pages is split by itself, and the doubtful cuts are listed to check', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScansFolder(WIDE_SCANS);
  const strip = page.getByTestId('strip-page');

  await test.step('the book has its pages as soon as the import is done, with no action of the reader', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of spreads');
    await uploadFolder(page, folder, WIDE_SCANS + 1);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/page-split`);
    await expect(page.getByTestId('stage-title')).toHaveText('Split');
    // Each wide scan is a left page and a right page, and the tall scan stays one page
    await expect(strip).toHaveCount(WIDE_SCANS * 2 + 1, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByTestId('split-banner')).toHaveCount(0);
  });

  await test.step('the cuts of the scans with no gutter in them are listed under Check, with the reason', async () => {
    await page.getByTestId('strip-filter-check').click();
    await expect(page).toHaveURL(/filter=check/);
    await expect(strip).toHaveCount(WIDE_SCANS * 2);
    await expect(page.getByTestId('strip-reason').first()).toContainText(
      'Gutter not found for certain',
    );
    await strip.first().click();
    await expect(page.getByTestId('this-page-review')).toContainText(
      'The gutter of this spread was not found for certain',
    );
    await expect(page.getByTestId('this-page-facts')).toContainText('Split intoTwo pages');
    await expect(page.getByTestId('this-page-facts')).toContainText('Slant of the cut');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

test('the choice of one page or two is kept through a run on all pages, and Auto gives the decision back', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScansFolder(WIDE_SCANS);
  const total = WIDE_SCANS * 2 + 1;
  const strip = page.getByTestId('strip-page');
  const runAll = async (): Promise<void> => {
    await page.getByTestId('run-menu').click();
    await page.getByTestId('run-all').click();
    await expect(page.getByTestId('run-summary')).toContainText('Every page is up to date', {
      timeout: RUN_TIMEOUT_MS,
    });
  };

  await test.step('the book is split by the import', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book with choices');
    await uploadFolder(page, folder, WIDE_SCANS + 1);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/page-split`);
    await expect(strip).toHaveCount(total, { timeout: RUN_TIMEOUT_MS });
  });

  await test.step('two pages for the tall scan cuts it at once, and a run on all pages keeps it cut', async () => {
    await strip.last().click();
    await expect(page.getByRole('radio', { name: 'One page' })).toBeChecked();
    await expect(page.getByTestId('split-automatic')).toBeVisible();
    await page.getByRole('radio', { name: 'Two pages' }).click();
    await expect(strip).toHaveCount(total + 1, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByTestId('split-chosen')).toHaveText('You chose: Two pages.');

    await runAll();
    await expect(strip).toHaveCount(total + 1);
    await expect(page.getByRole('radio', { name: 'Two pages' })).toBeChecked();
  });

  await test.step('one page for a spread asks first, and a run on all pages keeps the scan whole', async () => {
    await strip.first().click();
    await expect(page.getByRole('radio', { name: 'Two pages' })).toBeChecked();
    await page.getByRole('radio', { name: 'One page' }).click();
    const dialog = page.getByRole('dialog', { name: 'Go back to one page?' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Keep two pages' }).click();
    await expect(strip).toHaveCount(total + 1);

    await page.getByRole('radio', { name: 'One page' }).click();
    await page.getByTestId('unsplit-confirm').click();
    await expect(strip).toHaveCount(total, { timeout: RUN_TIMEOUT_MS });

    await runAll();
    await expect(strip).toHaveCount(total);
    await expect(page.getByRole('radio', { name: 'One page' })).toBeChecked();
  });

  await test.step('Auto deletes the choice, and the automatic split cuts the spread again', async () => {
    await expect(page.getByTestId('split-chosen')).toHaveText('You chose: One page.');
    await page.getByTestId('split-auto').click();
    await expect(strip).toHaveCount(total + 1, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByTestId('split-automatic')).toBeVisible();
    await expect(page.getByRole('radio', { name: 'Two pages' })).toBeChecked();
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
