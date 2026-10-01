import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import { createBook, registerAndSignIn, uploadFolder, writePagesFolder } from './support/account';

/**
 * Arranging the pages of a book: open a page from the strip, move it from the viewer, move groups and a whole file
 * from the book page, edit and number pages, add a placeholder and bind a scan to it, delete a page and a file, read
 * a refusal of the server, and see a change made in another tab arrive without a reload.
 */

const PAGES = 5;
const IMAGE_TIMEOUT_MS = 30_000;

/** The ids of the cards of the strip in the order they stand. */
async function stripOrder(page: Page): Promise<string[]> {
  return page
    .getByTestId('page-card')
    .evaluateAll((cards) => cards.map((card) => card.getAttribute('data-page-id') ?? ''));
}

function card(page: Page, id: string) {
  return page.locator(`[data-testid="page-card"][data-page-id="${id}"]`);
}

test('a reader arranges the pages of a book', async ({ page }) => {
  const folder = await writePagesFolder(PAGES);
  const cards = page.getByTestId('page-card');
  const caption = page.getByTestId('viewer-caption');
  let ids: string[] = [];

  await test.step('upload five pages and see them in the strip', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book to arrange');
    await uploadFolder(page, folder, PAGES);
    await expect(cards).toHaveCount(PAGES);
    ids = await stripOrder(page);
    for (const id of ids) {
      await expect(card(page, id).getByText('Text', { exact: true })).toBeVisible();
    }
    await expect(page.getByTestId('page-name')).toHaveText(['№ 1', '№ 2', '№ 3', '№ 4', '№ 5']);
  });

  await test.step('open a page from the strip, from a scan and from a file', async () => {
    await card(page, ids[2] ?? '')
      .getByRole('link')
      .click();
    await expect(page).toHaveURL(new RegExp(`viewer\\?page=${ids[2]}`));
    await expect(caption).toContainText('3 of 5');

    await page.goBack();
    await expect(cards).toHaveCount(PAGES);
    await page.getByRole('link', { name: 'Scan 2', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`viewer\\?page=${ids[1]}`));
    await expect(caption).toContainText('2 of 5');

    await page.goBack();
    await page
      .getByRole('link', { name: 'View the pages of book/page-04.png in the viewer' })
      .click();
    await expect(page).toHaveURL(new RegExp(`viewer\\?page=${ids[3]}`));
    await expect(caption).toContainText('4 of 5');
  });

  await test.step('move a page from the viewer, and keep the link to it', async () => {
    await page.getByRole('button', { name: 'Move page 4' }).click();
    await page.getByLabel('Page to put them next to').selectOption({ index: 3 });
    await page.getByLabel('Place', { exact: true }).selectOption('after');
    await page.getByRole('button', { name: 'Move', exact: true }).click();
    // The page keeps its id, so the address still names it, and it now stands last
    await expect(page).toHaveURL(new RegExp(`viewer\\?page=${ids[3]}`));
    await expect(caption).toContainText('5 of 5');
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');

    await page.getByRole('link', { name: 'Back to the book' }).click();
    await expect.poll(() => stripOrder(page)).toEqual([ids[0], ids[1], ids[2], ids[4], ids[3]]);
  });

  await test.step('move a group of pages after another page', async () => {
    await card(page, ids[0] ?? '')
      .getByRole('checkbox')
      .click();
    await card(page, ids[1] ?? '')
      .getByRole('checkbox')
      .click();
    await expect(page.getByTestId('selected-count')).toHaveText('2 selected');
    await page.getByRole('button', { name: 'Move selected' }).click();
    await expect(page.getByRole('heading', { name: 'Move 2 pages' })).toBeVisible();
    // The pages left to choose from are ids 2, 4 and 3 in book order, so the last is the page that stands last
    await page.getByLabel('Page to put them next to').selectOption({ index: 2 });
    await page.getByLabel('Place', { exact: true }).selectOption('after');
    await page.getByRole('button', { name: 'Move', exact: true }).click();
    await expect.poll(() => stripOrder(page)).toEqual([ids[2], ids[4], ids[3], ids[0], ids[1]]);
    await page.getByRole('button', { name: 'Clear the selection' }).click();
  });

  await test.step('read the refusal of the server when a move conflicts', async () => {
    await page.route('**/api/v1/projects/*/pages/move', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          detail: 'Another move took that place first.',
        }),
      }),
    );
    await card(page, ids[0] ?? '')
      .getByRole('checkbox')
      .click();
    await page.getByRole('button', { name: 'Move selected' }).click();
    await page.getByRole('button', { name: 'Move', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('Nothing was changed');
    await expect(page.getByRole('dialog')).toContainText('Another move took that place first.');
    await page.unroute('**/api/v1/projects/*/pages/move');
    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('button', { name: 'Clear the selection' }).click();
    // The order is the one the server has, which the refused move did not change
    await expect.poll(() => stripOrder(page)).toEqual([ids[2], ids[4], ids[3], ids[0], ids[1]]);
  });

  await test.step('edit the number, kind and inclusion of a page', async () => {
    await card(page, ids[2] ?? '')
      .getByRole('button', { name: /^Edit page/ })
      .click();
    await page.getByLabel('Printed number').fill('iv');
    await page.getByLabel('Kind of page').selectOption('plate');
    await page.getByLabel('Part of the book').uncheck();
    await page.getByLabel('Notes').fill('A colour plate');
    await page.getByRole('button', { name: 'Save', exact: true }).click();

    const edited = card(page, ids[2] ?? '');
    await expect(edited.getByTestId('page-name')).toHaveText('iv');
    await expect(edited.getByText('Plate', { exact: true })).toBeVisible();
    await expect(edited.getByText('Excluded', { exact: true })).toBeVisible();
  });

  await test.step('number the pages of the book, leaving the plate and excluded page alone', async () => {
    await page.getByRole('button', { name: 'Number pages' }).click();
    await page.getByRole('button', { name: 'Write numbers' }).click();
    await expect(page.getByTestId('page-name')).toHaveText(['iv', '1', '2', '3', '4']);
  });

  await test.step('add a placeholder at the end of the book', async () => {
    await page.getByRole('button', { name: 'Add a page' }).click();
    await page.getByLabel('Role in the book').selectOption('title');
    await page.getByLabel('Printed number').fill('i');
    await page.getByRole('button', { name: 'Add page' }).click();
    await expect(cards).toHaveCount(PAGES + 1);
    const added = cards.last();
    await expect(added.getByTestId('page-name')).toHaveText('i');
    await expect(added.getByText('Placeholder', { exact: true })).toBeVisible();
  });

  await test.step('bind a scan to the placeholder by taking it over', async () => {
    await cards
      .last()
      .getByRole('button', { name: /^Edit page/ })
      .click();
    await page.getByRole('button', { name: 'Bind a scan' }).click();
    await page.getByRole('button', { name: 'Scan 1', exact: true }).click();
    await page.getByLabel('Take the scan from the page that shows it now').check();
    await page.getByRole('button', { name: 'Bind scan' }).click();
    // The page that showed the scan is gone, and the placeholder became a page of its own
    await expect(cards).toHaveCount(PAGES);
    await expect(page.getByText('Placeholder', { exact: true })).toHaveCount(0);
    await expect(page.getByTestId('page-name').last()).toHaveText('i');
  });

  await test.step('move every page of a file to the start of the book', async () => {
    await page.getByRole('button', { name: 'Move the pages of book/page-02.png' }).click();
    await page.getByLabel('Page to put them next to').selectOption({ index: 0 });
    await page.getByLabel('Place', { exact: true }).selectOption('before');
    await page.getByRole('button', { name: 'Move', exact: true }).click();
    await expect.poll(async () => (await stripOrder(page))[0]).toBe(ids[1]);
  });

  await test.step('delete a file after the confirmation, and keep its page', async () => {
    await page.getByRole('button', { name: 'Delete book/page-03.png' }).click();
    await expect(page.getByText('Delete this file?')).toBeVisible();
    await page.getByRole('button', { name: 'Delete file' }).click();
    await expect(page.getByTestId('source-name')).toHaveCount(PAGES - 1);
    await expect(cards).toHaveCount(PAGES);
  });

  await test.step('delete a page after the confirmation', async () => {
    await cards
      .last()
      .getByRole('button', { name: /^Edit page/ })
      .click();
    await page.getByRole('button', { name: /^Delete page:/ }).click();
    await expect(page.getByText('Delete this page?')).toBeVisible();
    await page.getByRole('button', { name: 'Delete page', exact: true }).click();
    await expect(cards).toHaveCount(PAGES - 1);
  });

  await test.step('see a change made in another tab arrive without a reload', async () => {
    const other = await page.context().newPage();
    await other.goto(page.url());
    await expect(other.getByTestId('page-card')).toHaveCount(PAGES - 1);
    await other
      .getByTestId('page-card')
      .first()
      .getByRole('button', { name: /^Edit page/ })
      .click();
    await other.getByLabel('Printed number').fill('live');
    await other.getByRole('button', { name: 'Save', exact: true }).click();

    await expect(cards.first().getByTestId('page-name')).toHaveText('live');
    await other.close();
  });

  await test.step('add a blank leaf and watch its image appear when the job has written it', async () => {
    await page.getByRole('button', { name: 'Add a page' }).click();
    await page.getByLabel('Kind of new page').selectOption('blank');
    await page.getByLabel('Role in the book').selectOption('blank');
    await page.getByRole('button', { name: 'Add page' }).click();
    await expect(cards).toHaveCount(PAGES);

    const leaf = cards.last();
    await expect(leaf.getByText('Blank leaf', { exact: true })).toBeVisible();
    // A job writes the white image, and the event of its page version makes the strip read the manifest again
    await expect(leaf.getByRole('img')).toBeVisible({ timeout: IMAGE_TIMEOUT_MS });
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
