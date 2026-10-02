import { expect, type Page, test } from '@playwright/test';
import { createBook, registerAndSignIn } from './support/account';

/**
 * The About tab and the library: a description saves by itself and is still there after a reload, a field the
 * server would refuse is held back with its reason, the library card of the book draws its ten stages, and the book
 * is deleted from the last item of the tab after a confirmation.
 */

const TITLE = 'A book to describe';
const RENAMED = 'A book that was described';
const STAGE_COUNT = 10;

/** Wait until the line under the form says the changes were saved. */
async function expectSaved(page: Page): Promise<void> {
  await expect(page.getByTestId('autosave-status')).toHaveAttribute('data-state', 'saved');
}

test('a reader describes a book and finds the description after a reload', async ({ page }) => {
  let aboutUrl = '';

  await test.step('open the About tab of a new book', async () => {
    await registerAndSignIn(page);
    await createBook(page, TITLE);
    const projectId = new URL(page.url()).pathname.split('/')[2] ?? '';
    aboutUrl = `/projects/${projectId}/about`;
    await page.goto(aboutUrl);
    await expect(page.getByLabel('Title', { exact: true })).toHaveValue(TITLE);
  });

  await test.step('change the title and see it saved by itself, and kept after a reload', async () => {
    await page.getByLabel('Title', { exact: true }).fill(RENAMED);
    await expectSaved(page);
    await page.reload();
    await expect(page.getByLabel('Title', { exact: true })).toHaveValue(RENAMED);
  });

  await test.step('add a contributor and a language, and keep them after a reload', async () => {
    await page.getByRole('button', { name: 'Add a contributor' }).click();
    await page.getByLabel('Name as printed', { exact: true }).fill('Н. П. Соколовъ');
    await page.getByLabel('Languages', { exact: true }).fill('rus');
    await expectSaved(page);
    await page.reload();
    await expect(page.getByLabel('Name as printed', { exact: true })).toHaveValue('Н. П. Соколовъ');
    await expect(page.getByLabel('Role', { exact: true })).toHaveValue('aut');
    await expect(page.getByLabel('Languages', { exact: true })).toHaveValue('rus');
  });

  await test.step('hold back a language code the server would refuse and say why', async () => {
    await page.getByLabel('Languages', { exact: true }).fill('ru');
    await expect(page.getByText('Use three-letter codes such as rus or bel.')).toBeVisible();
    await expect(page.getByTestId('autosave-status')).toHaveAttribute('data-state', 'invalid');
    await page.getByLabel('Languages', { exact: true }).fill('rus bel');
    await expectSaved(page);
    await expect(page.getByText('Use three-letter codes such as rus or bel.')).toHaveCount(0);
  });

  await test.step('choose the image policy and keep the choice after a reload', async () => {
    await page.getByRole('radio', { name: /Lossless/ }).check();
    await expectSaved(page);
    await page.reload();
    await expect(page.getByRole('radio', { name: /Lossless/ })).toBeChecked();
    await expect(page.getByText('applies to the images written after it')).toBeVisible();
  });

  await test.step('see the library card with the ten stages and the next one', async () => {
    await page.goto('/projects');
    const card = page.getByTestId('project-card').filter({ hasText: RENAMED });
    await expect(card).toBeVisible();
    await expect(card.getByTestId('stage-segment')).toHaveCount(STAGE_COUNT);
    await expect(card.getByTestId('next-stage')).toContainText('Import');
  });

  await test.step('delete the book from the tab after the confirmation', async () => {
    await page.goto(aboutUrl);
    await page.getByRole('button', { name: 'Delete the book' }).click();
    await expect(page.getByText('Delete this book?')).toBeVisible();
    await page.getByRole('button', { name: 'Delete book', exact: true }).click();
    await expect(page).toHaveURL(/\/projects(\?.*)?$/);
    await expect(page.getByTestId('project-card').filter({ hasText: RENAMED })).toHaveCount(0);
  });
});
