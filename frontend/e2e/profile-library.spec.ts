import { readFile } from 'node:fs/promises';
import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  waitForIdleJobs,
  writePagesFolder,
} from './support/account';

/**
 * The library of profiles in the side panel of a book: it lists the profiles of the account with the number of books that
 * use each, applies one to the book and to the selected pages, copies one, saves one to a file and reads a file back, and
 * turns away a file that is not a profile. The profile menu lists the other profiles and opens the library.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 300_000;
const PROFILE_NAME = 'Photographed book';
const COPY_NAME = `${PROFILE_NAME} (copy)`;
const FILE_NAME = 'photographed-book.bookreviver-profile.json';
const BUILT_IN_STEPS = 5;

// Tall enough for the pictures of the key states to show the panel with its cards
test.use({ viewport: { width: 1280, height: 1000 } });

/** The cards of the library that hold a profile of the given name, oldest first. */
function cardsOf(page: Page, name: string) {
  return page.locator(`[data-testid="profile-row"][data-name="${name}"]`);
}

/** Create a book, upload the pages into it and open its Geometry stage. */
async function openNewGeometry(page: Page, title: string, folder: string): Promise<void> {
  await page.goto('/projects');
  await createBook(page, title);
  await uploadFolder(page, folder, PAGES);
  const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
  await page.goto(`${bookPath}/stages/geometry`);
  await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
  await expect(page.getByTestId('recipe-step').first()).toBeVisible();
}

/** Open the library from the entry of the profile menu. */
async function openLibrary(page: Page): Promise<void> {
  await page.getByTestId('profile-button').click();
  await page.getByTestId('profile-manage').click();
  await expect(page.getByTestId('profile-library-panel')).toBeVisible();
}

/** Shut the library and wait until it is gone, so nothing is left over the page. */
async function closeLibrary(page: Page): Promise<void> {
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('profile-library-panel')).toHaveCount(0);
}

test('the library applies, copies, exports and imports profiles and counts the books that use them', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);

  await test.step('the steps of a first book are kept as a profile', async () => {
    await registerAndSignIn(page);
    await openNewGeometry(page, 'A photographed book', folder);
    await page.getByTestId('profile-button').click();
    await page.getByTestId('profile-save-new').click();
    await page.getByLabel('Name of the profile').fill(PROFILE_NAME);
    await page.getByTestId('profile-save-submit').click();
    await expect(page.getByTestId('profile-saved')).toContainText(PROFILE_NAME);
  });

  await test.step('a second book finds the profile in the menu, and the library from it', async () => {
    await openNewGeometry(page, 'A flatbed book', folder);
    await expect(page.getByTestId('profile-name')).toHaveText('No profile');
    await page.getByTestId('profile-button').click();
    await expect(
      page.locator(`[data-testid="profile-switch"][data-name="${PROFILE_NAME}"]`),
    ).toBeVisible();
    await page.getByTestId('profile-manage').click();
    await expect(page.getByTestId('profile-library-panel')).toBeVisible();
    // The library opens on the stage of the book
    await expect(page.locator('[data-testid="profile-tab"][aria-selected="true"]')).toHaveAttribute(
      'data-tab',
      'geometry',
    );
    const card = cardsOf(page, PROFILE_NAME);
    await expect(card).toHaveCount(1);
    await expect(card.getByTestId('profile-books')).toHaveText('Used in 1 book');
    await expect(card.getByTestId('profile-steps')).toContainText('Perspective · Deskew');
    await snap(page, 'profile-library');
  });

  await test.step('a profile is duplicated, and the copy is used by no book', async () => {
    await cardsOf(page, PROFILE_NAME).getByTestId('profile-duplicate').click();
    const copy = cardsOf(page, COPY_NAME);
    await expect(copy).toHaveCount(1);
    await expect(copy.getByTestId('profile-books')).toHaveText('Used in no book');
  });

  let exported = '';
  await test.step('a profile is saved to a file, which holds its steps and no identifiers', async () => {
    const download = page.waitForEvent('download');
    await cardsOf(page, PROFILE_NAME).getByTestId('profile-export').click();
    const file = await download;
    expect(file.suggestedFilename()).toBe(FILE_NAME);
    exported = (await file.path()) ?? '';
    const content = JSON.parse(await readFile(exported, 'utf8')) as {
      version: number;
      stage: string;
      name: string;
      steps: Record<string, unknown>[];
    };
    expect([content.version, content.stage, content.name]).toEqual([1, 'geometry', PROFILE_NAME]);
    expect(content.steps).toHaveLength(BUILT_IN_STEPS);
    expect(content.steps.every((step) => !('step_id' in step))).toBe(true);
  });

  await test.step('the file is read back as a profile, and a file that is not one is turned away', async () => {
    await page.getByTestId('profile-import-file').setInputFiles(exported);
    await expect(page.getByTestId('profile-library-notice')).toContainText(
      `Imported the profile “${PROFILE_NAME}”`,
    );
    await expect(cardsOf(page, PROFILE_NAME)).toHaveCount(2);
    await page.getByTestId('profile-import-file').setInputFiles({
      name: 'notes.json',
      mimeType: 'application/json',
      buffer: Buffer.from('this is not a profile'),
    });
    await expect(page.getByTestId('profile-import-problem')).toContainText('does not hold JSON');
    await expect(cardsOf(page, PROFILE_NAME)).toHaveCount(2);
  });

  await test.step('the profile is applied to the book as its active recipe', async () => {
    await cardsOf(page, PROFILE_NAME).first().getByTestId('profile-apply-book').click();
    await expect(cardsOf(page, PROFILE_NAME).first().getByTestId('profile-notice')).toContainText(
      `Applied the profile “${PROFILE_NAME}”`,
    );
    await snap(page, 'profile-applied');
    await closeLibrary(page);
    await expect(page.getByTestId('profile-name')).toHaveText(PROFILE_NAME);
    await expect(page.getByTestId('recipe-select')).toContainText(`${PROFILE_NAME} · active`);
  });

  await test.step('two books use the profile now', async () => {
    await openLibrary(page);
    await expect(cardsOf(page, PROFILE_NAME).first().getByTestId('profile-books')).toHaveText(
      'Used in 2 books',
    );
    await closeLibrary(page);
  });

  await test.step('the profile is applied to the selected pages only, which the stage then runs on', async () => {
    await page.getByTestId('strip-view-switch').click();
    await expect(page).toHaveURL(/view=grid/);
    await openLibrary(page);
    // Nothing is selected yet, so the profile cannot be given to pages
    await expect(cardsOf(page, COPY_NAME).getByTestId('profile-apply-pages')).toBeDisabled();
    await closeLibrary(page);
    await page.getByTestId('strip-page').first().click();
    await expect(page.getByTestId('grid-selection')).toHaveText('1 page selected');
    await openLibrary(page);
    await cardsOf(page, COPY_NAME).getByTestId('profile-apply-pages').click();
    await expect(cardsOf(page, COPY_NAME).getByTestId('profile-notice')).toContainText('to 1 page');
    await closeLibrary(page);
    await waitForIdleJobs(page, openProjectId(page));
    await expect(page.getByTestId('recipe-select')).toContainText(COPY_NAME);
  });
});
