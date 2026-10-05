import { expect, type Page, test } from '@playwright/test';
import {
  createBook,
  registerAndSignIn,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import { isStepOn, toggleStepInWindow } from './support/steps';

/**
 * The profile a book was made from is visible where the steps are, in the step bar and its window: a built-in recipe names no profile, the steps are kept
 * as a new profile and the button names it, a switched-off step marks the book as changed and the menu says what changed,
 * reverting puts the steps of the profile back, and saving the changes to the profile saves the recipe of the book first and
 * ends the difference, which a reload of the page keeps.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const PROFILE_NAME = 'Photographed book';
const PERSPECTIVE = 'geometry.perspective';

// Tall enough for the pictures of the key states to show the profile button, the recipe and the menu
test.use({ viewport: { width: 1280, height: 1000 } });

/** Open the menu of the profile button. */
async function openMenu(page: Page): Promise<void> {
  await page.getByTestId('profile-button').click();
  await expect(page.getByTestId('profile-menu')).toBeVisible();
}

test('a book shows its profile, marks a change of the steps, and saves or reverts it', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);

  await test.step('a new book starts the Geometry stage with a built-in recipe, which is made from no profile', async () => {
    await registerAndSignIn(page);
    await page.goto('/projects');
    await createBook(page, 'A photographed book');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('bar-step').first()).toBeVisible();
    await expect(page.getByTestId('profile-name')).toHaveText('No profile');
    await expect(page.getByTestId('profile-changed')).toHaveCount(0);
  });

  await test.step('the steps are kept as a new profile, which the button names without a mark', async () => {
    await openMenu(page);
    await expect(page.getByTestId('profile-save')).toBeDisabled();
    await page.getByTestId('profile-save-new').click();
    await page.getByLabel('Name of the profile').fill(PROFILE_NAME);
    await page.getByTestId('profile-save-submit').click();
    await expect(page.getByTestId('profile-saved')).toContainText(PROFILE_NAME);
    await expect(page.getByTestId('profile-name')).toHaveText(PROFILE_NAME);
    await expect(page.getByTestId('profile-changed')).toHaveCount(0);
  });

  await test.step('a switched-off step marks the book as changed, and the menu says what changed', async () => {
    await toggleStepInWindow(page, PERSPECTIVE);
    await expect(page.getByTestId('profile-changed')).toBeVisible();
    await snap(page, 'profile-changed-mark');
    await openMenu(page);
    await expect(page.getByTestId('profile-changes')).toContainText('Perspective switched off');
    // Unsaved changes of the recipe do not keep the menu from saving to the profile
    await expect(page.getByTestId('profile-save')).toBeEnabled();
    await snap(page, 'profile-changed-menu');
  });

  await test.step('reverting puts the steps of the profile back and takes the mark off', async () => {
    await page.getByTestId('profile-revert').click();
    await expect(page.getByTestId('profile-changed')).toHaveCount(0);
    expect(await isStepOn(page, PERSPECTIVE)).toBe(true);
  });

  await test.step('saving to the profile saves the recipe of the book first, and the changes end', async () => {
    await toggleStepInWindow(page, PERSPECTIVE);
    await expect(page.getByTestId('profile-changed')).toBeVisible();
    await openMenu(page);
    await page.getByTestId('profile-save').click();
    await expect(page.getByTestId('profile-saved')).toContainText(PROFILE_NAME);
    await expect(page.getByTestId('profile-changed')).toHaveCount(0);
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
  });

  await test.step('after a reload the book still names the profile, with no mark and the step off', async () => {
    await page.reload();
    await expect(page.getByTestId('bar-step').first()).toBeVisible();
    await expect(page.getByTestId('profile-name')).toHaveText(PROFILE_NAME);
    await expect(page.getByTestId('profile-changed')).toHaveCount(0);
    expect(await isStepOn(page, PERSPECTIVE)).toBe(false);
  });
});
