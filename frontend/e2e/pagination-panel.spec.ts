import { expect, type Page, test } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../src/shared/http/csrf';
import {
  createBook,
  openOrderStage,
  openProjectId,
  registerAndSignIn,
  setKind,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * The pagination of the book on the Order stage: the panel that lists the sections, the colour of a section on the
 * thumbnails, a section started at the selected page, a section edited and deleted, a number written by hand that no
 * section takes back, and "Number pages" that makes a section.
 *
 * The sections of a book with a cover, a half title, a text and a plate are made through the API, since the panel and
 * the grid are what the scenario is about. The section of the text is made with the form, as a reader does.
 */

const PAGES = 8;
const COVER = 0;
const HALF_TITLE = 1;
const TEXT = 3;
const PLATE = 6;
const SCENARIO_TIMEOUT_MS = 240_000;

// Tall enough for the pictures of the key states to show the panel, the grid and the page that is selected
test.use({ viewport: { width: 1280, height: 1000 } });

/** The ids of the pages of the open book in book order, read from the server. */
async function pageIds(page: Page): Promise<string[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  return items.toSorted((a, b) => a.position - b.position).map((item) => item.id);
}

/** Make a pagination section of the open book through the API, as the signed-in reader. */
async function makeSection(page: Page, body: Record<string, unknown>): Promise<void> {
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  const response = await page.request.post(
    `/api/v1/projects/${openProjectId(page)}/pagination-sections`,
    { headers: { [CSRF_HEADER_NAME]: token }, data: body },
  );
  expect(response.status()).toBe(201);
}

test('a reader numbers a book by sections, sees them on the thumbnails and changes them from the panel', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  await registerAndSignIn(page);
  await createBook(page, 'A book with a preface');
  await uploadFolder(page, folder, PAGES);
  await setKind(page, PLATE, 'plate');

  const ids = await pageIds(page);
  const tiles = page.getByTestId('order-tile');
  const rows = page.getByTestId('section-row');
  const dialog = page.getByTestId('section-dialog');
  const numberOf = (position: number) => tiles.nth(position).getByTestId('page-number');
  const rowNamed = (name: string) => rows.filter({ has: page.getByText(name, { exact: true }) });

  await test.step('a book without sections asks for the first one', async () => {
    await openOrderStage(page);
    await expect(tiles).toHaveCount(PAGES);
    await expect(page.getByTestId('pagination-panel')).toBeVisible();
    await expect(page.getByTestId('sections-empty')).toBeVisible();
    await expect(numberOf(HALF_TITLE)).toHaveText('no number');
  });

  await test.step('the sections made through the API are listed in book order, with their numbers', async () => {
    await makeSection(page, {
      first_page_id: ids[COVER],
      name: 'Cover',
      style: 'arabic',
      display: 'not-counted',
    });
    await makeSection(page, {
      first_page_id: ids[COVER],
      name: 'Plates',
      style: 'roman-upper',
      prefix: 'Plate ',
      kinds: ['plate'],
    });
    await makeSection(page, {
      first_page_id: ids[HALF_TITLE],
      name: 'Half title and title',
      style: 'roman-lower',
      display: 'counted',
    });
    await page.reload();
    await expect(tiles).toHaveCount(PAGES);

    await expect(rows).toHaveCount(3);
    await expect(page.getByTestId('section-name')).toHaveText([
      'Cover',
      'Plates',
      'Half title and title',
    ]);
    await expect(page.getByTestId('section-numbers')).toHaveText(['—', 'Plate I', '[i]–[vi]']);
  });

  await test.step('a section starts at the selected page from the form', async () => {
    await tiles.nth(TEXT).click();
    await page.getByTestId('section-add').click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('Starts at')).toHaveValue(ids[TEXT] ?? '');

    await dialog.getByLabel('Name').fill('Text');
    await dialog.getByRole('button', { name: 'Save the section' }).click();
    await expect(dialog).toHaveCount(0);

    await expect(rows).toHaveCount(4);
    await expect(rowNamed('Text').getByTestId('section-numbers')).toHaveText('1–4');
    await expect(rowNamed('Text')).toContainText('Arabic');
  });

  await test.step('the pages carry the numbers of their sections, and a counted page shows its number in brackets', async () => {
    await expect(numberOf(COVER)).toHaveText('no number');
    await expect(numberOf(HALF_TITLE)).toHaveText('p. [i]');
    await expect(numberOf(2)).toHaveText('p. [ii]');
    await expect(numberOf(TEXT)).toHaveText('p. 1');
    await expect(numberOf(5)).toHaveText('p. 3');
    // The plate has a sequence of its own and does not use up a number of the text
    await expect(numberOf(PLATE)).toHaveText('p. Plate I');
    await expect(numberOf(7)).toHaveText('p. 4');
  });

  await test.step('the number of a page is ringed in the colour of its section', async () => {
    // Counted sections take the colours in the order of the book, a section that is not counted is grey
    await expect(numberOf(COVER)).toHaveClass(/border-gray-400/);
    await expect(numberOf(PLATE)).toHaveClass(/border-purple-500/);
    await expect(numberOf(HALF_TITLE)).toHaveClass(/border-blue-500/);
    await expect(numberOf(TEXT)).toHaveClass(/border-green-600/);
    await expect(tiles.nth(TEXT)).toHaveAttribute('data-section-id', /.+/);

    await page.getByRole('button', { name: 'Clear the selection' }).click();
    await snap(page, 'pagination-panel-with-sections');
    await snap(page, 'pagination-thumbnails-in-section-colours');
  });

  await test.step('a page counted but not printed shows its number in brackets in the panel of the page', async () => {
    await tiles.nth(HALF_TITLE).click();
    await expect(page.getByLabel('Printed number')).toHaveValue('[i]');
    await expect(numberOf(HALF_TITLE)).toHaveText('p. [i]');
    await snap(page, 'pagination-counted-not-printed-in-brackets');
  });

  await test.step('a number written by hand is marked and does not move the pages around it', async () => {
    await tiles.nth(4).click();
    await page.getByLabel('Printed number').fill('2a');
    await page.getByLabel('Printed number').blur();

    await expect(numberOf(4)).toHaveText('p. 2a');
    await expect(numberOf(4)).toHaveAttribute('data-manual', 'true');
    await expect(numberOf(5)).toHaveText('p. 3');
    await expect(numberOf(TEXT)).not.toHaveAttribute('data-manual', 'true');
  });

  await test.step('a section is changed from its row, and the pages follow', async () => {
    await rowNamed('Text').click();
    await expect(dialog.getByLabel('Name')).toHaveValue('Text');
    await dialog.getByLabel('First number').fill('5');
    await dialog.getByRole('button', { name: 'Save the section' }).click();
    await expect(dialog).toHaveCount(0);

    await expect(numberOf(TEXT)).toHaveText('p. 5');
    await expect(numberOf(5)).toHaveText('p. 7');
    await expect(rowNamed('Text').getByTestId('section-numbers')).toHaveText('5–8');
  });

  await test.step('a section the server refuses leaves the form open with its reason', async () => {
    await page.getByTestId('section-add').click();
    await dialog.getByLabel('Starts at').selectOption(ids[TEXT] ?? '');
    await dialog.getByRole('button', { name: 'Save the section' }).click();
    await expect(dialog).toContainText('Nothing was changed');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(rows).toHaveCount(4);
  });

  await test.step('deleting a section gives its pages to the section before it', async () => {
    await rowNamed('Half title and title').click();
    await dialog.getByRole('button', { name: 'Delete the section' }).click();
    await expect(dialog).toHaveCount(0);

    await expect(rows).toHaveCount(3);
    // The cover section does not count, so the pages that fell to it have no number
    await expect(numberOf(HALF_TITLE)).toHaveText('no number');
    await expect(numberOf(2)).toHaveText('no number');
  });

  await test.step('"Number pages" makes a section that the panel lists', async () => {
    await page.getByRole('button', { name: 'Number pages' }).click();
    await expect(page.getByTestId('numbering-panel')).toContainText('Makes a section');
    await page.getByRole('button', { name: 'Apply numbers' }).click();
    await expect(page.getByTestId('numbering-panel')).toHaveCount(0);

    await expect(rowNamed('Numbered pages')).toBeVisible();
  });
});
