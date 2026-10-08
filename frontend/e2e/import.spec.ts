import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  createBook,
  openImportStage,
  registerAndSignIn,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import { pageIds } from './support/page-work';

/**
 * The Import stage of a book with files: the list with its counts, the panel of the chosen file, a scan opened large
 * on the canvas, the way to the Order stage with the file named in the address, and the deletion of a file.
 */

const FILES = 4;

test('the Import stage lists the files, opens their scans and leads on to Order', async ({
  page,
}) => {
  const folder = await writePagesFolder(FILES);
  let importPath = '';

  await test.step('upload a folder into a new book', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book of files');
    await uploadFolder(page, folder, FILES);
    importPath = new URL(page.url()).pathname;
  });

  await test.step('the list counts the files and their scans', async () => {
    await expect(page.getByTestId('files-summary')).toContainText('4 files · 4 scans');
    await expect(page.getByTestId('source-name')).toHaveText([
      'book/page-01.png',
      'book/page-02.png',
      'book/page-03.png',
      'book/page-04.png',
    ]);
  });

  await test.step('the first file is chosen, and its panel states what it is', async () => {
    const panel = page.getByTestId('file-panel');
    await expect(page.getByTestId('source-row').first()).toHaveAttribute('aria-pressed', 'true');
    await expect(panel).toContainText('book/page-01.png');
    await expect(panel).toContainText('PNG image');
    await expect(panel).toContainText('Imported');
    await expect(page.getByTestId('file-pages')).toContainText(
      'Its 1 scan became page 1 of the book.',
    );
    await expect(page.getByTestId('scan-tile')).toHaveCount(1);
  });

  await test.step('choosing a file is in the address and survives a reload', async () => {
    await page.getByTestId('source-row').nth(2).click();
    await expect(page).toHaveURL(/[?&]source=/);
    await expect(page.getByTestId('file-panel')).toContainText('book/page-03.png');

    await page.reload();
    await expect(page.getByTestId('source-row').nth(2)).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('file-panel')).toContainText('book/page-03.png');
  });

  await test.step('a scan opens large on the canvas and the way back keeps the file', async () => {
    await page.getByTestId('scan-tile').click();
    await expect(page).toHaveURL(/[?&]scan=/);
    await expect(page.getByTestId('scan-viewer')).toBeVisible();
    await expect(page.getByTestId('viewer-canvas')).toHaveAttribute('data-state', 'ready');
    await expect(page.getByTestId('scan-caption')).toHaveText('Scan 1 of 1');
    // The panel of the file stays beside the large scan
    await expect(page.getByTestId('file-panel')).toContainText('book/page-03.png');

    await page.getByTestId('scan-back').click();
    await expect(page).not.toHaveURL(/[?&]scan=/);
    await expect(page.getByTestId('scan-grid')).toBeVisible();
    await expect(page.getByTestId('source-row').nth(2)).toHaveAttribute('aria-pressed', 'true');
  });

  await test.step('the pages of the file are shown in Order with the file in the address', async () => {
    const sourceId = new URL(page.url()).searchParams.get('source');
    expect(sourceId).not.toBeNull();

    await page.getByRole('link', { name: 'Show its pages in Order' }).click();
    await expect(page).toHaveURL(new RegExp(`/stages/page-order\\?source=${sourceId}$`));
    await expect(page.getByTestId('stage-title')).toHaveText('Order');

    await page.goto(importPath);
    await openImportStage(page);
    await page.getByTestId('source-row').nth(1).click();
    await page.getByRole('link', { name: 'Put its pages somewhere else…' }).click();
    await expect(page).toHaveURL(/\/stages\/page-order\?source=/);
  });

  await test.step('a file is deleted only after the question, which says what becomes of its pages', async () => {
    await openImportStage(page);
    await page.getByTestId('source-row').nth(3).click();
    await page.getByRole('button', { name: 'Delete this file…' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('The pages cut from it stay in the book with their images');

    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByTestId('source-name')).toHaveCount(FILES);

    await page.getByRole('button', { name: 'Delete this file…' }).click();
    await page.getByRole('button', { name: 'Delete file' }).click();
    await expect(page.getByTestId('source-name')).toHaveCount(FILES - 1);
    await expect(page.getByTestId('files-summary')).toContainText('3 files · 3 scans');
    // The pages cut from the file stay in the book
    expect(await pageIds(page)).toHaveLength(FILES);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
