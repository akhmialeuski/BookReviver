import { expect, type Page, test } from '@playwright/test';
import { MESSAGES } from '../src/shared/messages';
import {
  createBook,
  registerAndSignIn,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';

/**
 * A recipe set up in one book is kept as a profile of the account and applied in another: the steps are put in another
 * order and one is switched off, the profile is saved from the screen, applied in a second book, made the default for new
 * books in the settings of the account, and a third book starts the Geometry stage with it.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const PROFILE_NAME = 'Photographed book';
const RENAMED = 'Photographed pages';
const PERSPECTIVE = 'geometry.perspective';
const DESKEW = 'geometry.deskew';
const CROP = 'geometry.crop';

// Tall enough for the pictures of the key states to show the recipe panel with its steps
test.use({ viewport: { width: 1280, height: 1000 } });

/** A step as the panel draws it: the processor and whether the step is switched on. */
interface ScreenStep {
  processor: string;
  on: boolean;
}

/** Read the steps the recipe panel shows, in the order they are listed. */
async function stepsOnScreen(page: Page): Promise<ScreenStep[]> {
  return page.getByTestId('recipe-step').evaluateAll((items) =>
    items.map((item) => ({
      processor: item.getAttribute('data-processor') ?? '',
      on:
        item.querySelector('[data-testid="step-enabled"]')?.getAttribute('data-state') ===
        'checked',
    })),
  );
}

/** Open the Geometry stage of the open book, whose recipe panel lists the steps. */
async function openGeometry(page: Page, bookPath: string): Promise<void> {
  await page.goto(`${bookPath}/stages/geometry`);
  await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
  await expect(page.getByTestId('recipe-step').first()).toBeVisible();
}

/** Create a book from the library, upload the pages into it and return the address of the book. */
async function newBookWithPages(page: Page, title: string, folder: string): Promise<string> {
  await page.goto('/projects');
  await createBook(page, title);
  await uploadFolder(page, folder, PAGES);
  return new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
}

test('a recipe is saved as a profile, applied in another book, made the default, and a new book starts with it', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  let setUp: ScreenStep[] = [];

  await test.step('the steps of the first book are reordered and one is switched off', async () => {
    await registerAndSignIn(page);
    await openGeometry(page, await newBookWithPages(page, 'A photographed book', folder));
    expect((await stepsOnScreen(page)).map((step) => step.processor)).toEqual([
      PERSPECTIVE,
      DESKEW,
      CROP,
    ]);
    // The handle of the last step is lifted with Space, moved up twice with the arrow keys and dropped with Space
    const handle = page
      .locator(`[data-testid="recipe-step"][data-processor="${CROP}"]`)
      .getByRole('button', { name: /^Move the/ });
    await handle.focus();
    await page.keyboard.press('Space');
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('Space');
    await expect
      .poll(async () => (await stepsOnScreen(page)).map((step) => step.processor))
      .toEqual([CROP, PERSPECTIVE, DESKEW]);
    await page
      .locator(`[data-testid="recipe-step"][data-processor="${PERSPECTIVE}"]`)
      .getByTestId('step-enabled')
      .click();
    setUp = await stepsOnScreen(page);
    expect(setUp).toEqual([
      { processor: CROP, on: true },
      { processor: PERSPECTIVE, on: false },
      { processor: DESKEW, on: true },
    ]);
  });

  await test.step('the steps on the screen are saved as a profile, whether or not the recipe is saved', async () => {
    await page.getByTestId('profile-save-open').click();
    await expect(page.getByLabel('Name of the profile')).toBeVisible();
    await page.getByLabel('Name of the profile').fill(PROFILE_NAME);
    await page.getByTestId('profile-save-submit').click();
    await expect(page.getByTestId('profile-saved')).toContainText(PROFILE_NAME);
  });

  let secondBookPath = '';
  await test.step('another book applies the profile as a variant, with the same steps in the same order', async () => {
    secondBookPath = await newBookWithPages(page, 'A flatbed book', folder);
    await openGeometry(page, secondBookPath);
    // The built-in recipe is the active one and has the steps in their own order
    expect((await stepsOnScreen(page)).map((step) => step.processor)).toEqual([
      PERSPECTIVE,
      DESKEW,
      CROP,
    ]);
    await page.getByTestId('profile-apply-open').click();
    await expect(page.getByTestId('profile-apply-select')).toContainText(PROFILE_NAME);
    await snap(page, 'apply-profile-dialog');
    await page.getByTestId('profile-apply-submit').click();
    await expect(page.getByTestId('profile-applied')).toContainText(PROFILE_NAME);
    await expect(page.getByTestId('profile-left-out')).toHaveCount(0);
    await expect(page.getByTestId('recipe-select')).toContainText(`${PROFILE_NAME} · 0 pages`);
    await expect.poll(() => stepsOnScreen(page)).toEqual(setUp);
    // Applied as a variant, the profile leaves the built-in recipe the active one
    await expect(page.getByTestId('recipe-active')).toHaveCount(0);
  });

  await test.step('the profile is made the default for new books and renamed in the settings of the account', async () => {
    await page.getByRole('button', { name: MESSAGES.workspace.header.accountMenu }).click();
    await page.getByTestId('account-profiles').click();
    await expect(page.getByRole('heading', { name: 'Recipe profiles' })).toBeVisible();
    const row = page.locator(`[data-testid="profile-row"][data-name="${PROFILE_NAME}"]`);
    await expect(row.getByTestId('profile-steps')).toContainText('3 steps');
    await row.getByTestId('profile-make-default').click();
    await expect(row.getByTestId('profile-default-badge')).toBeVisible();
    await row.getByTestId('profile-rename').click();
    await page.getByLabel('New name').fill(RENAMED);
    await page.getByTestId('profile-rename-submit').click();
    const renamed = page.locator(`[data-testid="profile-row"][data-name="${RENAMED}"]`);
    await expect(renamed).toHaveAttribute('data-default', 'true');
    await expect(renamed.getByTestId('profile-default-badge')).toBeVisible();
    // The picture is taken once the dialog has faded out
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await snap(page, 'profiles-in-account-settings');
  });

  await test.step('a new book starts the Geometry stage with the default profile instead of the built-in recipe', async () => {
    const thirdBookPath = await newBookWithPages(page, 'A third book', folder);
    await openGeometry(page, thirdBookPath);
    await expect(page.getByTestId('recipe-active')).toBeVisible();
    await expect(page.getByTestId('recipe-select')).toContainText(`${RENAMED} · active`);
    await expect.poll(() => stepsOnScreen(page)).toEqual(setUp);
    // The first step is open, which pushes the others out of the picture, so it is closed for the picture
    await page.getByTestId('recipe-step').first().getByTestId('step-toggle').click();
    await expect(page.getByTestId('recipe-step').last()).toBeVisible();
    await snap(page, 'new-book-starts-with-default-profile');
  });

  await test.step('the book that was open before the default was chosen keeps its own recipes', async () => {
    await openGeometry(page, secondBookPath);
    await expect(page.getByTestId('recipe-select')).not.toContainText(`${RENAMED} · active`);
  });
});
