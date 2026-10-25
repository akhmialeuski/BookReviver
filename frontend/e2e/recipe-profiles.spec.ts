import { expect, type Page, test } from '@playwright/test';
import { MESSAGES } from '../src/shared/messages';
import {
  createBook,
  registerAndSignIn,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import { closeStepsWindow, openStepsWindow, windowStepOf } from './support/steps';

/**
 * A recipe set up in a book is kept as a profile of the book: the steps are put in another order and one is switched off,
 * the steps on the screen are kept as a profile from the profile menu in one go, and the profile is renamed in the
 * library of the book.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const PROFILE_NAME = 'Photographed book';
const RENAMED = 'Photographed pages';
const PERSPECTIVE = 'geometry.perspective';
const DESKEW = 'geometry.deskew';
const DEWARP = 'geometry.dewarp';
const CROP = 'geometry.crop';
const NORMALIZE = 'geometry.normalize';
// The steps of the built-in recipe a new book starts the Geometry stage with, in their order, with the titles the window
// announces while one of them is moved
const BUILT_IN_STEPS = [
  { processor: PERSPECTIVE, title: 'Perspective' },
  { processor: DESKEW, title: 'Deskew' },
  { processor: DEWARP, title: 'Dewarp' },
  { processor: CROP, title: 'Select content' },
  { processor: NORMALIZE, title: 'Margins' },
];
const BUILT_IN = BUILT_IN_STEPS.map((step) => step.processor);

// Tall enough for the pictures of the key states to show the bar and the panel of the recipe
test.use({ viewport: { width: 1280, height: 1000 } });

/** A step as the window of the gear draws it: the processor and whether the step is switched on. */
interface ScreenStep {
  processor: string;
  on: boolean;
}

/** Read the steps the open window of the gear lists, in the order they are listed. */
async function stepsInWindow(page: Page): Promise<ScreenStep[]> {
  return page.getByTestId('recipe-step').evaluateAll((items) =>
    items.map((item) => ({
      processor: item.getAttribute('data-processor') ?? '',
      on:
        item.querySelector('[data-testid="step-enabled"]')?.getAttribute('data-state') ===
        'checked',
    })),
  );
}

/** Open the window of the gear, read the steps it lists, and shut it. */
async function stepsOnScreen(page: Page): Promise<ScreenStep[]> {
  await openStepsWindow(page);
  const steps = await stepsInWindow(page);
  await closeStepsWindow(page);
  return steps;
}

/** Open the Geometry stage of the open book, whose bar lists the steps. */
async function openGeometry(page: Page, bookPath: string): Promise<void> {
  await page.goto(`${bookPath}/stages/geometry`);
  await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
  await expect(page.getByTestId('bar-step').first()).toBeVisible();
}

/** Create a book from the library, upload the pages into it and return the address of the book. */
async function newBookWithPages(page: Page, title: string, folder: string): Promise<string> {
  await page.goto('/books');
  await createBook(page, title);
  await uploadFolder(page, folder, PAGES);
  return new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
}

test('a recipe is saved as a profile and the profile is renamed in the library of the book', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  let setUp: ScreenStep[] = [];

  await test.step('the steps of the book are reordered and one is switched off', async () => {
    await registerAndSignIn(page);
    await openGeometry(page, await newBookWithPages(page, 'A photographed book', folder));
    expect((await stepsOnScreen(page)).map((step) => step.processor)).toEqual(BUILT_IN);
    // The handle of the crop in the window of the gear is lifted with Space, moved up with the arrow keys and dropped with
    // Space. Each key waits for what the list announces to a screen reader, since a key pressed before the list took the
    // last one is lost
    await openStepsWindow(page);
    const handle = windowStepOf(page, CROP).getByRole('button', { name: /^Move the/ });
    const announced = (text: string) =>
      expect(page.getByRole('status').filter({ hasText: text })).toHaveCount(1);
    const drag = MESSAGES.processing.steps.drag;
    await handle.focus();
    await page.keyboard.press('Space');
    // The region keeps the last announcement only, and a step that is picked up is over its own place at once
    await announced(drag.over('Select content'));
    for (const over of BUILT_IN_STEPS.slice(0, BUILT_IN.indexOf(CROP)).toReversed()) {
      await page.keyboard.press('ArrowUp');
      await announced(drag.over(over.title));
    }
    await page.keyboard.press('Space');
    await announced(drag.dropped('Select content'));
    expect((await stepsInWindow(page)).map((step) => step.processor)).toEqual([
      CROP,
      PERSPECTIVE,
      DESKEW,
      DEWARP,
      NORMALIZE,
    ]);
    await windowStepOf(page, PERSPECTIVE).getByTestId('step-enabled').click();
    setUp = await stepsInWindow(page);
    expect(setUp).toEqual([
      { processor: CROP, on: true },
      { processor: PERSPECTIVE, on: false },
      { processor: DESKEW, on: true },
      { processor: DEWARP, on: true },
      { processor: NORMALIZE, on: true },
    ]);
    await closeStepsWindow(page);
  });

  await test.step('the steps on the screen are kept as a profile, which saves the recipe of the book first', async () => {
    await page.getByTestId('profile-button').click();
    await page.getByTestId('profile-save-new').click();
    await expect(page.getByLabel('Name of the profile')).toBeVisible();
    await page.getByLabel('Name of the profile').fill(PROFILE_NAME);
    await page.getByTestId('profile-save-submit').click();
    await expect(page.getByTestId('profile-saved')).toContainText(PROFILE_NAME);
  });

  await test.step('the profile is renamed in the library of the book', async () => {
    await page.getByTestId('profile-button').click();
    await page.getByTestId('profile-manage').click();
    await expect(page.getByTestId('profile-library-panel')).toBeVisible();
    const row = page.locator(`[data-testid="profile-row"][data-name="${PROFILE_NAME}"]`);
    await expect(row.getByTestId('profile-steps')).toContainText(
      'Select content · Perspective (off) · Deskew',
    );
    await snap(page, 'profile-in-library');
    await row.getByTestId('profile-rename').click();
    await page.getByLabel('New name').fill(RENAMED);
    await page.getByTestId('profile-rename-submit').click();
    const renamed = page.locator(`[data-testid="profile-row"][data-name="${RENAMED}"]`);
    await expect(renamed).toHaveCount(1);
    // The picture is taken once the dialog has faded out
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await snap(page, 'profile-renamed');
  });
});
