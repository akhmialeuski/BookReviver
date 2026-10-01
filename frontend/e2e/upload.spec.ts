import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { confirmationLink, PASSWORD } from './support/account';
import { solidPng } from './support/png';

/**
 * The first journey of a reader: register, confirm the address from the mailed link, sign in, create a book, upload
 * a folder of page images with a system file and an unsupported file in it, rearrange it, and see the book with its
 * pages once the import has run.
 */

const PAGE_SIZE = 40;

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
    await expect(page.getByText('0 pages')).toBeVisible();
  });

  await test.step('choose the folder and check the list before sending', async () => {
    await page.getByRole('button', { name: 'Upload files' }).click();
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
    await expect(page.getByTestId('job-state')).toHaveText('Finished', { timeout: 60_000 });

    await expect(page.getByTestId('import-status')).toContainText(
      '4 sources imported into the book.',
    );
    const rejected = page.getByTestId('rejected-files');
    await expect(rejected).toContainText('book/vol2/broken.png');
    await expect(rejected).toContainText('Cannot be read');
  });

  await test.step('the book shows its pages, sources in the order that was chosen, and scans', async () => {
    await expect(page.getByText('4 pages')).toBeVisible();
    await expect(page.getByTestId('source-name')).toHaveText([
      'book/vol2/a-1.png',
      'book/vol1/1.png',
      'book/vol1/2.png',
      'book/vol1/10.png',
    ]);
    await expect(page.getByRole('img', { name: /^Scan \d+$/ })).toHaveCount(4);
  });

  await test.step('sign out closes the book again', async () => {
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/sign-in/);
    await page.goBack();
    await expect(page).toHaveURL(/\/sign-in\?redirect=/);
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
