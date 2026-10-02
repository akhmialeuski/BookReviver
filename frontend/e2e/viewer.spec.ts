import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { createBook, registerAndSignIn, uploadFolder, writePagesFolder } from './support/account';

/**
 * Reading a book in the viewer: open it from the header of the book, turn pages with the buttons, the keys, the slider and
 * the go-to field, show a spread, and come back to the same view by reloading the address.
 */

const PAGES = 6;

test('a reader turns the pages of a book in the viewer', async ({ page }) => {
  const folder = await writePagesFolder(PAGES);
  const canvas = page.getByTestId('viewer-canvas');
  const caption = page.getByTestId('viewer-caption');

  await test.step('upload six pages into a new book', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to read');
    await uploadFolder(page, folder, PAGES);
    await expect(page.getByTestId('stage-page-order')).toContainText('6 pages');
  });

  await test.step('open the viewer on the first page', async () => {
    await page.getByRole('link', { name: 'Read the book' }).click();
    await expect(page).toHaveURL(/\/projects\/[^/]+\/viewer$/);
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(caption).toContainText('1 of 6');
    await expect(page.getByTestId('page-panel').getByRole('button')).toHaveCount(PAGES);
  });

  await test.step('turn pages with the buttons and the keys', async () => {
    await page.getByRole('button', { name: 'Next page' }).click();
    await expect(caption).toContainText('2 of 6');
    await expect(page).toHaveURL(/viewer\?page=/);
    await expect(canvas).toHaveAttribute('data-state', 'ready');

    await page.keyboard.press('ArrowRight');
    await expect(caption).toContainText('3 of 6');
    await page.keyboard.press('ArrowLeft');
    await expect(caption).toContainText('2 of 6');
    await page.keyboard.press('End');
    await expect(caption).toContainText('6 of 6');
    await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled();
    await page.keyboard.press('Home');
    await expect(caption).toContainText('1 of 6');
    await expect(page.getByRole('button', { name: 'Previous page' })).toBeDisabled();
  });

  await test.step('go to a position with the field and the slider', async () => {
    await page.getByLabel('Go to position').fill('4');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await expect(caption).toContainText('4 of 6');

    await page.getByTestId('page-slider').focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expect(caption).toContainText('2 of 6');
    await expect(canvas).toHaveAttribute('data-state', 'ready');
  });

  await test.step('open a page from the panel and collapse the panel', async () => {
    await page.getByTestId('page-panel').getByRole('button').nth(4).click();
    await expect(caption).toContainText('5 of 6');
    await expect(page.getByTestId('page-panel').getByRole('button').nth(4)).toHaveAttribute(
      'aria-current',
      'page',
    );

    await page.getByRole('button', { name: 'Hide the page panel' }).click();
    await expect(page.getByTestId('page-panel')).toHaveCount(0);
    await page.getByRole('button', { name: 'Show the page panel' }).click();
    await expect(page.getByTestId('page-panel')).toBeVisible();
  });

  await test.step('fit the page, fit the width and zoom', async () => {
    await page.getByRole('button', { name: 'Fit to width' }).click();
    await expect(page.getByRole('button', { name: 'Fit to width' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await page.getByRole('button', { name: 'Zoom in' }).click();
    await page.getByRole('button', { name: 'Zoom out' }).click();
    await page.getByRole('button', { name: 'Fit the whole page' }).click();
    await expect(page.getByRole('button', { name: 'Fit the whole page' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  await test.step('show two pages at once, and keep the view across a reload', async () => {
    await page.getByRole('button', { name: 'One page' }).click();
    await expect(page).toHaveURL(/spread=true/);
    // The first page lies alone and the pairs follow it, so page 5 is the right half of the pair 4 and 5
    await expect(caption).toContainText('4 of 6');
    await expect(caption).toContainText('5 of 6');
    await expect(canvas).toHaveAttribute('data-state', 'ready');

    await page.keyboard.press('ArrowLeft');
    await expect(caption).toContainText('2 of 6');
    await expect(caption).toContainText('3 of 6');

    await page.reload();
    await expect(canvas).toHaveAttribute('data-state', 'ready');
    await expect(caption).toContainText('2 of 6');
    await expect(caption).toContainText('3 of 6');
    await expect(page.getByRole('button', { name: 'Two pages' })).toBeVisible();
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
