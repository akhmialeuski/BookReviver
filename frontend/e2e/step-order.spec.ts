import { rm } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import { MESSAGES } from '../src/shared/messages';
import {
  createBook,
  openProjectId,
  registerAndSignIn,
  snap,
  uploadFolder,
  writePagesFolder,
} from './support/account';
import {
  closeStepsWindow,
  openStepsWindow,
  processorsInWindow,
  stepsWindow,
  windowStepOf,
} from './support/steps';

/**
 * The order of the steps of the Geometry recipe, which is set in the window of the gear: a step dragged off its usual place
 * is marked with the reason, in the window and in the panel of the step, and put back by "Restore the usual order" without a
 * change of settings, a place where a step cannot work is refused while dragging with the reason, and the free order lets
 * the step stand and saves the recipe with a warning.
 */

const PAGES = 2;
const SCENARIO_TIMEOUT_MS = 240_000;
const PERSPECTIVE = 'geometry.perspective';
const DESKEW = 'geometry.deskew';
const DEWARP = 'geometry.dewarp';
const CROP = 'geometry.crop';
const NORMALIZE = 'geometry.normalize';
// The steps of the built-in recipe a new book starts the Geometry stage with, in their order, with the titles the list
// announces while one of them is moved
const BUILT_IN_STEPS = [
  { processor: PERSPECTIVE, title: 'Perspective' },
  { processor: DESKEW, title: 'Deskew' },
  { processor: DEWARP, title: 'Dewarp' },
  { processor: CROP, title: 'Select content' },
  { processor: NORMALIZE, title: 'Margins' },
];
const BUILT_IN = BUILT_IN_STEPS.map((step) => step.processor);
const CROP_PLACE = BUILT_IN.indexOf(CROP);
const drag = MESSAGES.processing.steps.drag;

// Tall enough for the pictures of the key states to show the window of the steps
test.use({ viewport: { width: 1280, height: 1000 } });

/** Wait for the line the list announces to a screen reader, which is how a key pressed in a drag is known to be taken. */
async function announced(page: Page, text: string): Promise<void> {
  await expect(page.getByRole('status').filter({ hasText: text })).toHaveCount(1);
}

/**
 * Lift the step of a processor with Space and move it up by the arrow key as many places as it is asked to, waiting for
 * what the list announces after each key, and leave it held. The step is over its own place as soon as it is lifted. The
 * announcement of the last key is the one given, for a place the list answers otherwise than by naming the step it is over.
 */
async function liftAndMoveUp(
  page: Page,
  processor: string,
  places: number,
  lastAnnouncement?: string,
): Promise<void> {
  const index = BUILT_IN.indexOf(processor);
  const titleAt = (place: number): string => BUILT_IN_STEPS[index - place]?.title ?? '';
  await windowStepOf(page, processor)
    .getByRole('button', { name: /^Move the/ })
    .focus();
  await page.keyboard.press('Space');
  await announced(page, drag.over(titleAt(0)));
  for (let place = 1; place <= places; place += 1) {
    await page.keyboard.press('ArrowUp');
    await announced(
      page,
      place === places && lastAnnouncement !== undefined
        ? lastAnnouncement
        : drag.over(titleAt(place)),
    );
  }
}

test('a step off its usual place is marked and put back, a place where it cannot work is refused, and the free order allows it', async ({
  page,
}) => {
  test.setTimeout(SCENARIO_TIMEOUT_MS);
  const folder = await writePagesFolder(PAGES);
  const window = stepsWindow(page);
  const marks = window.getByTestId('step-order-mark');

  await test.step('a new book opens the Geometry stage with its steps in the usual order, none of them marked', async () => {
    await registerAndSignIn(page);
    await createBook(page, 'A book for the order of steps');
    await uploadFolder(page, folder, PAGES);
    const bookPath = new URL(page.url()).pathname.replace(/\/stages\/import$/, '');
    await page.goto(`${bookPath}/stages/geometry`);
    await expect(page.getByTestId('stage-title')).toHaveText('Geometry');
    await expect(page.getByTestId('bar-step').first()).toBeVisible();
    // The steps are in the window of the gear only, and the panel of the recipe lists none
    await expect(page.getByTestId('recipe-steps')).toHaveCount(0);
    await openStepsWindow(page);
    expect(await processorsInWindow(page)).toEqual(BUILT_IN);
    await expect(marks).toHaveCount(0);
  });

  await test.step('Select content is dragged above Perspective, which is allowed and marked with the reason', async () => {
    await liftAndMoveUp(page, CROP, CROP_PLACE);
    await page.keyboard.press('Space');
    await announced(page, drag.dropped('Select content'));
    expect((await processorsInWindow(page))[0]).toBe(CROP);
    const mark = windowStepOf(page, CROP).getByTestId('step-order-mark');
    await expect(mark).toHaveText('Out of place');
    await expect(mark).toHaveAttribute('data-kind', 'usual');
    await expect(windowStepOf(page, CROP).getByTestId('step-order-reason')).toContainText(
      'Select content',
    );
    await expect(marks).toHaveCount(1);
    await snap(page, 'step-out-of-its-usual-place');
    await closeStepsWindow(page);
  });

  await test.step('"Restore the usual order" in the panel of the step puts every step back and changes no setting', async () => {
    await page.getByTestId('bar-step').filter({ hasText: 'Select content' }).click();
    const details = page.getByTestId('step-order-details');
    await expect(details).toBeVisible();
    await expect(details).toContainText('Select content');
    await page.getByTestId('step-restore-order').click();
    await expect(details).toHaveCount(0);
    await openStepsWindow(page);
    expect(await processorsInWindow(page)).toEqual(BUILT_IN);
    await expect(marks).toHaveCount(0);
    // The draft is the saved recipe again, so there is nothing to save
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);
  });

  await test.step('Margins is dragged above Select content, which is refused with the reason, and the step stays', async () => {
    await liftAndMoveUp(page, NORMALIZE, 1, 'This place is not allowed.');
    const notice = page.getByTestId('order-refusal');
    await expect(notice).toHaveAttribute('data-mode', 'usual');
    await expect(notice).toContainText('This place is not allowed.');
    await expect(notice).toContainText('cannot come before Select content');
    await expect(windowStepOf(page, CROP)).toHaveClass(/border-destructive/);
    await snap(page, 'place-refused-with-its-reason');
    await page.keyboard.press('Space');
    await announced(page, drag.cancelled);
    await expect(notice).toHaveCount(0);
    expect(await processorsInWindow(page)).toEqual(BUILT_IN);
  });

  await test.step('in the free order the same drop is allowed with a warning, and the recipe is saved with it', async () => {
    await window.getByTestId('order-free').click();
    await liftAndMoveUp(page, NORMALIZE, 1);
    const notice = page.getByTestId('order-refusal');
    await expect(notice).toHaveAttribute('data-mode', 'free');
    await expect(notice).toContainText('Allowed in the free order.');
    await page.keyboard.press('Space');
    await announced(page, drag.dropped('Margins'));
    const placed = [...BUILT_IN];
    placed.splice(CROP_PLACE, 2, NORMALIZE, CROP);
    expect(await processorsInWindow(page)).toEqual(placed);
    await expect(windowStepOf(page, NORMALIZE).getByTestId('step-order-mark')).toHaveAttribute(
      'data-kind',
      'required',
    );
    await window.getByTestId('recipe-save').click();
    await expect(page.getByTestId('recipe-save-bar')).toHaveCount(0);

    const saved = await page.request.get(
      `/api/v1/projects/${openProjectId(page)}/stages/geometry/recipe`,
    );
    const recipe = (await saved.json()) as {
      steps: { processor_key: string }[];
      order_issues: { kind: string; processor_key: string }[];
    };
    expect(recipe.steps.map((step) => step.processor_key)).toEqual(placed);
    expect(recipe.order_issues).toMatchObject([{ kind: 'required', processor_key: NORMALIZE }]);
    // A recipe saved in the free order is opened in it, so the next save is not refused
    await expect(window.getByTestId('order-free')).toHaveAttribute('data-state', 'checked');
  });

  await rm(path.dirname(folder), { recursive: true, force: true });
});
