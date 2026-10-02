import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { SERVER_LOG } from './env';
import { solidPng } from './png';

/**
 * The steps every scenario starts with: a confirmed account that is signed in, a book, and a folder of page images
 * uploaded into it.
 *
 * The first journey in `upload.spec.ts` walks through these steps screen by screen. The later scenarios only need
 * their result, so they take it from here and spend their time on what they are about.
 */

export const PASSWORD = 'correct horse battery staple';
const PAGE_SIZE = 40;
const IMPORT_TIMEOUT_MS = 60_000;

/** Wait for the mail of the registration and return the confirmation link it holds. */
export async function confirmationLink(): Promise<string> {
  let link = '';
  await expect
    .poll(async () => {
      const log = await readFile(SERVER_LOG, 'utf8').catch(() => '');
      link = log.match(/https?:\/\/\S+\/verify-email\?token=\S+/g)?.at(-1) ?? '';
      return link;
    })
    .not.toBe('');
  return link;
}

/** Register a new reader, confirm the address from the mailed link and sign in. */
export async function registerAndSignIn(page: Page): Promise<void> {
  const email = `reader-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto('/register');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByText('Check your mail')).toBeVisible();

  const link = await confirmationLink();
  await page.goto(new URL(link).pathname + new URL(link).search);
  await expect(page.getByText('Your address is confirmed')).toBeVisible();

  await page.getByRole('link', { name: 'Go to sign in' }).click();
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Your books' })).toBeVisible();
}

/** Create a book with a title, which opens on its first stage with the title in the header. */
export async function createBook(page: Page, title: string): Promise<void> {
  await page.getByRole('button', { name: 'New book' }).click();
  await page.getByLabel('Title', { exact: true }).fill(title);
  await page.getByRole('button', { name: 'Create book' }).click();
  await expect(page.getByRole('heading', { name: title })).toBeVisible();
}

/**
 * Write a folder of solid-colour page images, `page-01.png`, `page-02.png` and so on, and return its path.
 *
 * @param count Number of pages.
 */
export async function writePagesFolder(count: number): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'book');
  await mkdir(root, { recursive: true });
  for (let number = 1; number <= count; number += 1) {
    // A colour of its own for every page, so the server does not take one for a duplicate of another
    const color = [(number * 37) % 256, (number * 91) % 256, (number * 53) % 256] as const;
    await writeFile(
      path.join(root, `page-${String(number).padStart(2, '0')}.png`),
      solidPng(PAGE_SIZE, PAGE_SIZE + number, color),
    );
  }
  return root;
}

/** Open the Import stage of the open book, which shows its files and their scans, or the drop area of an empty book. */
export async function openImportStage(page: Page): Promise<void> {
  await page.getByTestId('stage-import').click();
  await expect(page).toHaveURL(/\/projects\/[^/]+\/stages\/import(\?|$)/);
  await expect(page.getByTestId('stage-screen')).toHaveAttribute('data-stage', 'import');
  await expect(page.getByTestId('stage-title')).toHaveText('Import');
}

/**
 * Open the Order stage of the open book, which shows the page strip and its actions until the Order workspace
 * replaces it.
 */
export async function openOrderStage(page: Page): Promise<void> {
  await page.getByTestId('stage-page-order').click();
  await expect(page).toHaveURL(/\/projects\/[^/]+\/stages\/page-order(\?|$)/);
  await expect(page.getByTestId('order-bridge')).toBeVisible();
}

/**
 * Upload a folder into the open book, which has no files yet, and wait until its import has finished.
 *
 * The drop area of the empty book starts the import as soon as the folder is chosen, so there is no dialog to answer.
 * The wait ends when the book lists every file and no import row is left.
 *
 * @param files Number of files in the folder, each of which becomes a file of the book.
 */
export async function uploadFolder(page: Page, folder: string, files: number): Promise<void> {
  await openImportStage(page);
  await page.getByTestId('folder-input').setInputFiles(folder);
  await expect(page.getByTestId('source-name')).toHaveCount(files, {
    timeout: IMPORT_TIMEOUT_MS,
  });
  await expect(page.getByTestId('import-row')).toHaveCount(0);
}
