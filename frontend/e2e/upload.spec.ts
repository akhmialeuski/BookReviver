import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { confirmationLink, PASSWORD } from './support/account';
import { solidPng } from './support/png';

/**
 * The first journey of a reader: register, confirm the address from the mailed link, sign in, create a book, drop a
 * first file on the empty Import stage, add a folder of page images with a system file and an unsupported file in it
 * through the dialog, rearrange it, and see the files of the book, their scans and the pages once the import has run.
 */

const PAGE_SIZE = 40;
const COVER_COLOR = [120, 60, 20] as const;

// Page images by their path in the chosen folder, each with a colour of its own so none repeats another
const PAGES: ReadonlyArray<readonly [string, readonly [number, number, number]]> = [
  ['vol1/1.png', [200, 40, 40]],
  ['vol1/2.png', [40, 200, 40]],
  ['vol1/10.png', [40, 40, 200]],
  ['vol2/a-1.png', [200, 200, 40]],
  ['vol2/a-2.png', [40, 200, 200]],
];

/** Write the folder the scenario uploads and return its path. */
async function writeBookFolder(): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'book');
  await mkdir(path.join(root, 'vol1'), { recursive: true });
  await mkdir(path.join(root, 'vol2'), { recursive: true });
  for (const [name, color] of PAGES) {
    await writeFile(path.join(root, name), solidPng(PAGE_SIZE, PAGE_SIZE, color));
  }
  // A page the server cannot read, two system files and a file of a type no source can be
  await writeFile(path.join(root, 'vol2', 'broken.png'), 'this is not an image');
  await writeFile(path.join(root, 'vol1', 'Thumbs.db'), 'thumbnails');
  await writeFile(path.join(root, 'vol1', '._1.png'), 'resource fork');
  await writeFile(path.join(root, 'vol2', 'notes.txt'), 'not a page');
  return root;
}

test('a reader uploads a folder and sees the book with its pages', async ({ page }) => {
  const folder = await writeBookFolder();
  const email = `reader-${Date.now()}@example.com`;

  await test.step('a visitor is sent to the sign-in page', async () => {
    await page.goto('/projects');
    await expect(page).toHaveURL(/\/sign-in\?redirect=%2Fprojects$/);
  });

  await test.step('register and confirm the address from the mailed link', async () => {
    await page.getByRole('link', { name: 'Create one' }).click();
    // Both screens have an "Email address" field, so without this wait the text goes into the sign-in form that is
    // still on the screen and the register form then opens empty. The card title is a div, not a heading.
    await expect(page.getByText('Create an account', { exact: true })).toBeVisible();
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.getByText('Check your mail')).toBeVisible();

    const link = await confirmationLink();
    await page.goto(new URL(link).pathname + new URL(link).search);
    await expect(page.getByText('Your address is confirmed')).toBeVisible();
  });

  await test.step('sign in and create a book', async () => {
    await page.getByRole('link', { name: 'Go to sign in' }).click();
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('heading', { name: 'Your books' })).toBeVisible();
    await expect(page.getByText('You have no books yet')).toBeVisible();

    await page.getByRole('button', { name: 'New book' }).click();
    await page.getByLabel('Title', { exact: true }).fill('An old primer');
    await page.getByRole('button', { name: 'Create book' }).click();
    await expect(page.getByRole('heading', { name: 'An old primer' })).toBeVisible();
    await expect(page).toHaveURL(/\/projects\/[^/]+\/stages\/import$/);
  });

  await test.step('the Import stage has no files yet and the book waits for pages', async () => {
    await expect(page.getByTestId('import-empty')).toBeVisible();
    await expect(page.getByText('Drop a folder or files here')).toBeVisible();
    await expect(page.getByTestId('import-tips')).toContainText('Good to know');
    await expect(page.getByTestId('stage-import')).toContainText('No files yet');
    await expect(page.getByTestId('stage-page-order')).toContainText('Waits for pages');
  });

  await test.step('a file dropped on the empty stage is imported without a dialog', async () => {
    // A synthetic drop has no folder entries, so the zone reads the plain file, as it does for a dropped file
    const dropped = await page.evaluateHandle(
      (bytes) => {
        const transfer = new DataTransfer();
        transfer.items.add(new File([new Uint8Array(bytes)], 'cover.png', { type: 'image/png' }));
        return transfer;
      },
      Array.from(solidPng(PAGE_SIZE, PAGE_SIZE, COVER_COLOR)),
    );
    await page.getByTestId('drop-zone').dispatchEvent('drop', { dataTransfer: dropped });

    await expect(page.getByTestId('source-name')).toHaveText(['cover.png'], { timeout: 60_000 });
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByTestId('import-row')).toHaveCount(0);
    await expect(page.getByTestId('files-summary')).toContainText('1 file · 1 scan');
    await expect(page.getByTestId('stage-import')).toContainText('1 file · 1 scan');
  });

  await test.step('choose the folder and check the list before sending', async () => {
    await page.getByRole('button', { name: 'Add files' }).click();
    await page.getByTestId('folder-input').setInputFiles(folder);

    // Natural order: 10 comes after 2, and the folders follow each other
    await expect(page.getByTestId('file-name')).toHaveText([
      '1.png',
      '2.png',
      '10.png',
      'a-1.png',
      'a-2.png',
      'broken.png',
    ]);
    await expect(page.getByText('6 files to upload')).toBeVisible();

    // The two system files and the text file are named and not sent
    await page.getByTestId('skipped-files').getByText('3 files skipped').click();
    const skipped = page.getByTestId('skipped-files');
    await expect(skipped).toContainText('book/vol1/Thumbs.db');
    await expect(skipped).toContainText('book/vol1/._1.png');
    await expect(skipped).toContainText('System file');
    await expect(skipped).toContainText('book/vol2/notes.txt');
    await expect(skipped).toContainText('This type of file cannot be imported');
  });

  await test.step('take out a file and move a folder', async () => {
    await page.getByRole('button', { name: 'Remove a-2.png' }).click();
    await expect(page.getByText('5 files to upload')).toBeVisible();

    await page.getByRole('button', { name: 'Move the folder book/vol2 up' }).click();
    await expect(page.getByTestId('file-name')).toHaveText([
      'a-1.png',
      'broken.png',
      '1.png',
      '2.png',
      '10.png',
    ]);
  });

  await test.step('send and follow the import to its end', async () => {
    await page.getByRole('button', { name: 'Upload 5 files' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    // The report of the import stays under the list until it is closed
    const status = page.getByTestId('import-status');
    await expect(status.getByTestId('job-state')).toHaveText('Finished', { timeout: 60_000 });
    await expect(status).toContainText('4 files imported into the book.');
    const rejected = page.getByTestId('rejected-files');
    await expect(rejected).toContainText('book/vol2/broken.png');
    await expect(rejected).toContainText('Cannot be read');
    await expect(page.getByTestId('import-row')).toHaveCount(0);
  });

  await test.step('the book lists its files in the order that was chosen, and counts their pages', async () => {
    await expect(page.getByTestId('stage-page-order')).toContainText('5 pages');
    await expect(page.getByTestId('source-name')).toHaveText([
      'cover.png',
      'book/vol2/a-1.png',
      'book/vol1/1.png',
      'book/vol1/2.png',
      'book/vol1/10.png',
    ]);
    await expect(page.getByTestId('stage-import')).toContainText('5 files · 5 scans');
  });

  await test.step('choose a file to see its scans and what became of its pages', async () => {
    // The first file is chosen until another is
    await expect(page.getByTestId('source-row').first()).toHaveAttribute('aria-pressed', 'true');

    await page.getByTestId('source-row').nth(2).click();
    await expect(page).toHaveURL(/[?&]source=/);
    await expect(page.getByTestId('source-row').nth(2)).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('file-panel')).toContainText('book/vol1/1.png');
    await expect(page.getByTestId('file-pages')).toContainText(
      'Its 1 scan became page 3 of the book.',
    );
    await expect(page.getByTestId('scan-tile')).toHaveCount(1);
    await expect(page.getByRole('img', { name: 'Scan 1' })).toBeVisible();
  });

  await test.step('close the report of the import', async () => {
    await page.getByTestId('import-status').getByRole('button', { name: 'Dismiss' }).click();
    await expect(page.getByTestId('import-status')).toHaveCount(0);
  });

  await test.step('sign out closes the book again', async () => {
    await page.getByRole('button', { name: 'Account menu' }).click();
    await page.getByRole('menuitem', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/sign-in/);
    await page.goBack();
    await expect(page).toHaveURL(/\/sign-in\?redirect=/);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
