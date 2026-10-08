import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../../src/shared/http/csrf';
import { SERVER_LOG } from './env';
import { platePng, sheetPng, solidPng } from './png';

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
const JOBS_TIMEOUT_MS = 90_000;
// The most pages one batch request adds, which is the limit of the server
const PAGES_PER_BATCH = 1000;

/**
 * Wait for a mail to the given address in the server log and return the link of the given page that it holds.
 *
 * The log mailer writes one record for each mail, which begins with `Mail to <address>:` and holds the whole body, so
 * the link is read from the record of that address and never from whatever mail was written last. The scenarios share
 * one server, and two of them register at the same time.
 *
 * @param email The address the mail was sent to.
 * @param route The page the link opens, such as `verify-email` or `reset-password`.
 */
export async function mailedLink(email: string, route: string): Promise<string> {
  const linkPattern = new RegExp(`https?://\\S+/${route}\\?token=\\S+`);
  const recordStart = `Mail to ${email}:`;
  let link = '';
  await expect
    .poll(async () => {
      const log = await readFile(SERVER_LOG, 'utf8').catch(() => '');
      // Each record starts at its own "Mail to", so a chunk of the log never holds the link of another record
      const records = log.split(/(?=Mail to )/).filter((record) => record.startsWith(recordStart));
      link = records.map((record) => record.match(linkPattern)?.[0] ?? '').findLast(Boolean) ?? '';
      return link;
    })
    .not.toBe('');
  return link;
}

/** Wait for the mail of the registration of the given address and return the confirmation link it holds. */
export function confirmationLink(email: string): Promise<string> {
  return mailedLink(email, 'verify-email');
}

/**
 * Register a new reader, confirm the address from the mailed link and sign in.
 *
 * @returns The address the reader registered with, so another browser can sign in as the same account.
 */
export async function registerAndSignIn(page: Page): Promise<string> {
  const email = `reader-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto('/register');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByText('Check your mail')).toBeVisible();

  const link = await confirmationLink(email);
  await page.goto(new URL(link).pathname + new URL(link).search);
  await expect(page.getByText('Your address is confirmed')).toBeVisible();

  await page.getByRole('link', { name: 'Go to sign in' }).click();
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Your books' })).toBeVisible();
  return email;
}

/** Sign in with an address that was registered earlier, such as from a second browser of the same reader. */
export async function signIn(page: Page, email: string): Promise<void> {
  await page.goto('/sign-in');
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

const PLATE_SCAN_SIZE = { width: 420, height: 580 };

/**
 * Write a folder of four scans, a solid page, a plate in colour, a plate in black and white and another solid page, in
 * that order, and return its path. The solid pages show no picture, and a plate is a picture of continuous tones that
 * covers most of the page, which the detection of the content of the pages tells from text.
 */
export async function writeMixedFolder(): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'mixed');
  await mkdir(root, { recursive: true });
  const pages = [
    solidPng(PAGE_SIZE, PAGE_SIZE + 1, [228, 208, 164]),
    platePng(PLATE_SCAN_SIZE.width, PLATE_SCAN_SIZE.height, 5, true),
    platePng(PLATE_SCAN_SIZE.width, PLATE_SCAN_SIZE.height, 3),
    solidPng(PAGE_SIZE, PAGE_SIZE + 2, [220, 200, 150]),
  ];
  for (const [index, content] of pages.entries()) {
    await writeFile(path.join(root, `page-${String(index + 1).padStart(2, '0')}.png`), content);
  }
  return root;
}

const SHEET_SCAN_SIZE = { width: 420, height: 580 };

/**
 * Write a folder of scans of a sheet of paper with lines of words on a dark binding, which the Geometry stage can find,
 * straighten and cut to the frame of its words, and return its path.
 *
 * @param count Number of scans.
 * @param options `scale` makes the scans larger, with the words on them larger in step, and `dust` puts that many specks
 * of dust on the paper of each.
 */
export async function writeSheetsFolder(
  count: number,
  { scale = 1, dust = 0 }: { scale?: number; dust?: number } = {},
): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'sheets');
  await mkdir(root, { recursive: true });
  for (let number = 1; number <= count; number += 1) {
    await writeFile(
      path.join(root, `sheet-${String(number).padStart(2, '0')}.png`),
      sheetPng(SHEET_SCAN_SIZE.width * scale, SHEET_SCAN_SIZE.height * scale, number * 7919, {
        textScale: scale,
        dust,
      }),
    );
  }
  return root;
}

/**
 * Write a folder of scans of sheets with lines of words, each taken at its own scale of the text as a scan and a
 * photograph of one book are, and return its path. A scan of a larger scale is larger by the same factor, so the sheet and
 * its words keep their proportions and only the size of the text differs.
 *
 * @param scales The scale of the text of each scan, such as `[1, 1.2]`.
 */
export async function writeScaledSheetsFolder(scales: readonly number[]): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'scaled-sheets');
  await mkdir(root, { recursive: true });
  for (const [index, scale] of scales.entries()) {
    await writeFile(
      path.join(root, `sheet-${String(index + 1).padStart(2, '0')}.png`),
      sheetPng(
        Math.round(SHEET_SCAN_SIZE.width * scale),
        Math.round(SHEET_SCAN_SIZE.height * scale),
        (index + 1) * 7919,
        { textScale: scale },
      ),
    );
  }
  return root;
}

/**
 * Write a folder of scans of a sheet of paper whose lines are bent into the gutter, as a page of a thick book is, and
 * return its path.
 *
 * @param count Number of scans.
 * @param bendPx How far the right edge of the scene is moved down.
 */
export async function writeBentSheetsFolder(count: number, bendPx: number): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'bent');
  await mkdir(root, { recursive: true });
  for (let number = 1; number <= count; number += 1) {
    await writeFile(
      path.join(root, `bent-${String(number).padStart(2, '0')}.png`),
      sheetPng(SHEET_SCAN_SIZE.width, SHEET_SCAN_SIZE.height, number * 7919, { bendPx }),
    );
  }
  return root;
}

const SCAN_SIZE = { width: 160, height: 90 };
const TALL_SCAN_SIZE = { width: 60, height: 90 };

/**
 * Write a folder of wide solid-colour scans, which look like open books, and one tall page, and return its path.
 *
 * @param wideScans Number of scans wider than tall.
 */
export async function writeScansFolder(wideScans: number): Promise<string> {
  const root = path.join(await mkdtemp(path.join(tmpdir(), 'bookreviver-')), 'scans');
  await mkdir(root, { recursive: true });
  for (let number = 1; number <= wideScans; number += 1) {
    const color = [(number * 61) % 256, (number * 17) % 256, 200] as const;
    await writeFile(
      path.join(root, `scan-${number}.png`),
      solidPng(SCAN_SIZE.width, SCAN_SIZE.height, color),
    );
  }
  await writeFile(
    path.join(root, 'tall.png'),
    solidPng(TALL_SCAN_SIZE.width, TALL_SCAN_SIZE.height, [10, 200, 30]),
  );
  return root;
}

/** Return the identifier of the book open in the page, read from its address. */
export function openProjectId(page: Page): string {
  const id = new URL(page.url()).pathname.match(/\/projects\/([^/]+)/)?.[1];
  if (id === undefined) {
    throw new Error(`No book is open at ${page.url()}.`);
  }
  return id;
}

/** A recipe of a stage as the API gives it: the kind of page it is for, its steps and what is wrong with their order. */
export interface StoredRecipe {
  kind: string;
  steps: { processor_key: string; step_id: string }[];
  order_issues: { kind: string; processor_key: string }[];
}

/**
 * Read the recipe of a kind of page of a stage of the open book. A stage has one recipe for each kind of page, each with
 * steps of its own.
 *
 * @param kind The kind of page the recipe is for, `text` unless the scenario is about another kind.
 */
export async function recipeOf(page: Page, stage: string, kind = 'text'): Promise<StoredRecipe> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/stages/${stage}/recipes`,
  );
  const recipe = ((await response.json()) as { items: StoredRecipe[] }).items.find(
    (entry) => entry.kind === kind,
  );
  if (recipe === undefined) {
    throw new Error(`The stage ${stage} has no recipe for pages of the kind ${kind}.`);
  }
  return recipe;
}

/**
 * Read the identifiers of the steps of the recipe of a kind of page of a stage that run a processor, in the order of the
 * recipe. A manual edit is addressed by the identifier of its step, which the saves of a scenario are told apart by.
 *
 * @param kind The kind of page the recipe is for, `text` unless the scenario is about another kind.
 */
export async function stepIdsOf(
  page: Page,
  stage: string,
  processor: string,
  kind = 'text',
): Promise<string[]> {
  return (await recipeOf(page, stage, kind)).steps
    .filter((step) => step.processor_key === processor)
    .map((step) => step.step_id);
}

/** The place a reader left a book at, as the server holds it. */
export interface StoredPlace {
  mode: string;
  stage: string;
  page_id: string | null;
  scan_id: string | null;
  source_id: string | null;
  view: string;
  compare: string;
  filter: string;
  canvas: { zoom: number; centre_x: number; centre_y: number } | null;
  strip_page_id: string | null;
}

/**
 * Read the place the signed-in reader left a book at straight from the server.
 *
 * @returns The status of the answer, and the place when there is one: none for a book the reader has not worked on
 * (204) and for a book that is not theirs (404).
 */
export async function readPlace(
  page: Page,
  projectId: string,
): Promise<{ status: number; place: StoredPlace | null }> {
  const response = await page.request.get(`/api/v1/projects/${projectId}/place`);
  const status = response.status();
  return { status, place: status === 200 ? ((await response.json()) as StoredPlace) : null };
}

/**
 * Wait until the book has no job queued or running, as the server counts them.
 *
 * A scenario that changes something a run reads, one change after another, waits with this between them, since the server
 * refuses a run while another is going and the screen cannot know of a job before it is told. The collection of old
 * versions that follows a run is stored with the end of the run, so the list is never empty between the two.
 */
export async function waitForIdleJobs(page: Page, projectId: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/api/v1/projects/${projectId}/jobs?active=true&size=20`,
        );
        const body = (await response.json()) as { items: unknown[] };
        return body.items.length;
      },
      { timeout: JOBS_TIMEOUT_MS },
    )
    .toBe(0);
}

/** Change the kind of the page at a position of the open book, as the Order stage does, straight through the API. */
export async function setKind(page: Page, position: number, kind: string): Promise<void> {
  const projectId = openProjectId(page);
  const listed = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  const target = items.find((item) => item.position === position);
  if (target === undefined) {
    throw new Error(`The book has no page at position ${position}.`);
  }
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  const response = await page.request.patch(`/api/v1/projects/${projectId}/pages/${target.id}`, {
    headers: { [CSRF_HEADER_NAME]: token },
    data: { kind },
  });
  expect(response.ok()).toBe(true);
}

/**
 * Say by hand that every page of the open book is a page of text, but the pages at the positions that are left out.
 *
 * The scans the sheet fixtures draw show a sheet on a dark table, which the detection of the content reads as a picture,
 * and a picture is processed by the recipe for pictures, not by the steps of text. A scenario about the steps of a page of
 * text says so, and the detection leaves a content type that was set by hand alone.
 */
export async function markPagesAsText(page: Page, leftOut: readonly number[] = []): Promise<void> {
  const projectId = openProjectId(page);
  const listed = await page.request.get(`/api/v1/projects/${projectId}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  for (const item of items.filter((entry) => !leftOut.includes(entry.position))) {
    const response = await page.request.patch(`/api/v1/projects/${projectId}/pages/${item.id}`, {
      headers: { [CSRF_HEADER_NAME]: token },
      data: { content_type: 'text' },
    });
    expect(response.ok()).toBe(true);
  }
}

/** Send a mutating request to the API as the signed-in reader, with the CSRF header the browser would add. */
export async function deleteAsReader(page: Page, apiPath: string): Promise<number> {
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  const response = await page.request.delete(apiPath, { headers: { [CSRF_HEADER_NAME]: token } });
  return response.status();
}

/**
 * Append placeholder pages labelled with their numbers to the open book, a thousand to a request, as the Order stage
 * does, and come back to the book on the given stage. A long book is made this way, since importing hundreds of scans
 * would spend the run on the import.
 *
 * The browser leaves the book meanwhile, because the open book would read its pages again after the pages are added.
 *
 * @param stage The stage the book opens on afterwards, such as `geometry` or `page-order`.
 */
export async function addPlaceholderPages(page: Page, count: number, stage: string): Promise<void> {
  const projectId = openProjectId(page);
  await page.goto('about:blank');
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '';
  for (let first = 1; first <= count; first += PAGES_PER_BATCH) {
    const last = Math.min(first + PAGES_PER_BATCH - 1, count);
    const data = Array.from({ length: last - first + 1 }, (_, offset) => ({
      origin: 'placeholder',
      kind: 'text',
      label: String(first + offset),
    }));
    const response = await page.request.post(`/api/v1/projects/${projectId}/pages/batch`, {
      headers: { [CSRF_HEADER_NAME]: token },
      data,
    });
    expect(response.ok()).toBe(true);
  }
  await page.goto(`/projects/${projectId}/stages/${stage}`);
}

/**
 * Capture a key state of a scenario for the task report: the visible part of the page, saved as `<name>.png` in the
 * output folder of the running test, under `frontend/test-results/`.
 *
 * @param name What the picture shows, in kebab-case, such as `strip-first-page`.
 */
export async function snap(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: test.info().outputPath(`${name}.png`), fullPage: false });
}

/** Open the Import stage of the open book, which shows its files and their scans, or the drop area of an empty book. */
export async function openImportStage(page: Page): Promise<void> {
  await page.getByTestId('stage-import').click();
  await expect(page).toHaveURL(/\/projects\/[^/]+\/stages\/import(\?|$)/);
  await expect(page.getByTestId('stage-screen')).toHaveAttribute('data-stage', 'import');
  await expect(page.getByTestId('stage-title')).toHaveText('Import');
}

/** Open the Order stage of the open book, which shows its pages as a grid. */
export async function openOrderStage(page: Page): Promise<void> {
  await page.getByTestId('stage-page-order').click();
  await expect(page).toHaveURL(/\/projects\/[^/]+\/stages\/page-order(\?|$)/);
  await expect(page.getByTestId('order-grid')).toBeVisible();
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
  // The job goes on after the last file is listed, so its row needs the same time as the import
  await expect(page.getByTestId('import-row')).toHaveCount(0, { timeout: IMPORT_TIMEOUT_MS });
  // The import starts the split of the new pages, and until it ends a later stage has no page and refuses a run
  await waitForIdleJobs(page, openProjectId(page));
}
