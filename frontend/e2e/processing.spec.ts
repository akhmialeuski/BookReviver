import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { createBook, registerAndSignIn, uploadFolder, writePagesFolder } from './support/account';
import { solidPng } from './support/png';

/**
 * The processing workspace on the Geometry stage: the recipe drawn from the schema of its processor, a preview of the
 * open page that is asked for once and not again, the compare of the page before and after, a run on all pages, the
 * Check filter with the reason under each page, and the choice of an earlier result of a page. Then the Split stage:
 * the filter and the banner of the scans wider than tall, one run that cuts them, and the question before a scan goes
 * back to one page.
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
    await expect(page.getByTestId('recipe-select')).toContainText('Deskew');
    await expect(page.getByTestId('recipe-active')).toBeVisible();
    await expect(page.getByTestId('recipe-step')).toHaveCount(1);
    await expect(page.getByTestId('recipe-step')).toContainText('1 · Deskew');
    // The fields carry the titles of the schema, and no name of the code
    await expect(page.getByRole('spinbutton', { name: 'Largest slant' })).toHaveValue('5');
    await expect(page.getByRole('slider', { name: 'Least confidence' })).toBeVisible();
    await expect(page.getByTestId('stage-panel')).not.toContainText('max_angle');
    // The steps that are planned stand under the real ones
    await expect(page.getByTestId('coming-steps')).toContainText('Perspective crop');
    await expect(page.getByTestId('coming-steps')).toContainText('Soon');
  });

  await test.step('a value outside its limits cannot be saved', async () => {
    const slant = page.getByRole('spinbutton', { name: 'Largest slant' });
    await slant.fill('99');
    await expect(slant).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByTestId('recipe-save')).toBeDisabled();
    await expect(page.getByTestId('preview-toggle')).toBeDisabled();
    await slant.fill('5');
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

    const slant = page.getByRole('spinbutton', { name: 'Largest slant' });
    await slant.fill('7');
    await expect(page.getByTestId('preview-working')).toBeVisible();
    await expect(page.getByTestId('preview-working')).toBeHidden({ timeout: RUN_TIMEOUT_MS });
    expect(previews).toHaveLength(2);

    // Back to the settings that were shown first: the preview made for them is shown again
    await slant.fill('5');
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
    await expect(page.getByRole('button', { name: 'Set by hand' })).toBeDisabled();
    await expect(page.getByTestId('this-page-facts')).toContainText('Confidence');
  });

  await test.step('a changed recipe says how many pages it makes out of date and is saved by the button', async () => {
    await page.getByRole('spinbutton', { name: 'Largest slant' }).fill('9');
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
    await expect(entries.nth(1)).toContainText('Largest slant 5');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});

const WIDE_SCANS = 3;
const SCAN_SIZE = { width: 160, height: 90 };

/** Write a folder of wide solid-colour scans and one tall page, and return its path. */
async function writeScansFolder(): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'scans');
  await mkdir(root, { recursive: true });
  for (let number = 1; number <= WIDE_SCANS; number += 1) {
    const color = [(number * 61) % 256, (number * 17) % 256, 200] as const;
    await writeFile(
      path.join(root, `scan-${number}.png`),
      solidPng(SCAN_SIZE.width, SCAN_SIZE.height, color),
    );
  }
  await writeFile(path.join(root, 'tall.png'), solidPng(60, 90, [10, 200, 30]));
  return root;
}

test('a reader cuts the wide scans on the Split stage and goes back to one page after a confirmation', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writeScansFolder();
  const total = WIDE_SCANS + 1;
  const strip = page.getByTestId('strip-page');

  await test.step('the Split stage offers the scans wider than tall', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of spreads');
    await uploadFolder(page, folder, total);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/page-split`);
    await expect(page.getByTestId('stage-title')).toHaveText('Split');
    await expect(strip).toHaveCount(total);
    await expect(page.getByTestId('strip-filter-wide')).toContainText(`Wide ${WIDE_SCANS}`);
    await expect(page.getByTestId('split-banner')).toContainText(
      `${WIDE_SCANS} scans are wider than tall`,
    );

    await page.getByTestId('strip-filter-wide').click();
    await expect(page).toHaveURL(/filter=wide/);
    await expect(strip).toHaveCount(WIDE_SCANS);
    await page.getByTestId('strip-filter-all').click();
  });

  await test.step('one run of the banner cuts every wide scan in two', async () => {
    await page.getByTestId('split-banner-cut').click();
    // Each wide scan becomes a left page and a right page
    await expect(strip).toHaveCount(WIDE_SCANS * 2 + 1, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByTestId('split-banner')).toHaveCount(0);
    await expect(page.getByTestId('strip-filter-wide')).toContainText(`Wide ${WIDE_SCANS * 2}`);
  });

  await test.step('going back to one page asks first, and a refusal changes nothing', async () => {
    await page.getByTestId('strip-filter-wide').click();
    await strip.first().click();
    await expect(page.getByRole('radio', { name: 'Two pages' })).toBeChecked();

    await page.getByRole('radio', { name: 'One page' }).click();
    const dialog = page.getByRole('dialog', { name: 'Go back to one page?' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Keep two pages' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole('radio', { name: 'Two pages' })).toBeChecked();
    await expect(strip).toHaveCount(WIDE_SCANS * 2);
  });

  await test.step('a confirmation sends the run, and the scan is one page again', async () => {
    await page.getByRole('radio', { name: 'One page' }).click();
    await page.getByTestId('unsplit-confirm').click();
    await expect(strip).toHaveCount(WIDE_SCANS * 2 - 1, { timeout: RUN_TIMEOUT_MS });
    await expect(page.getByRole('radio', { name: 'One page' })).toBeChecked();
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
