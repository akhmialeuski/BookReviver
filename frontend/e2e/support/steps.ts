import { expect, type Locator, type Page } from '@playwright/test';

/**
 * The steps of a stage with a step bar, as a scenario reaches them: the window of the gear, which holds the order, the
 * conditions, the switches and the removal of the steps, and the bar with the panel of the open step.
 */

/** The window of the gear. */
export function stepsWindow(page: Page): Locator {
  return page.getByTestId('steps-window');
}

/** Open the window of the gear and return it. */
export async function openStepsWindow(page: Page): Promise<Locator> {
  await page.getByTestId('steps-gear').click();
  const window = stepsWindow(page);
  await expect(window).toBeVisible();
  return window;
}

/** Shut the window of the gear with its button, and wait until nothing of it is left over the page. */
export async function closeStepsWindow(page: Page): Promise<void> {
  await stepsWindow(page).getByRole('button', { name: 'Close' }).click();
  await expect(stepsWindow(page)).toHaveCount(0);
}

/** The row of the first step of a processor in the window of the gear. */
export function windowStepOf(page: Page, processor: string): Locator {
  return stepsWindow(page)
    .locator(`[data-testid="recipe-step"][data-processor="${processor}"]`)
    .first();
}

/** Switch the first step of a processor on or off in the window of the gear, which is left shut. */
export async function toggleStepInWindow(page: Page, processor: string): Promise<void> {
  await openStepsWindow(page);
  await windowStepOf(page, processor).getByTestId('step-enabled').click();
  await closeStepsWindow(page);
}

/** Tell whether the first step of a processor is switched on, as the window of the gear draws it. */
export async function isStepOn(page: Page, processor: string): Promise<boolean> {
  await openStepsWindow(page);
  const state = await windowStepOf(page, processor)
    .getByTestId('step-enabled')
    .getAttribute('data-state');
  await closeStepsWindow(page);
  return state === 'checked';
}

/** The processors of the steps the window of the gear lists, in the order they are listed. */
export async function processorsInWindow(page: Page): Promise<string[]> {
  return stepsWindow(page)
    .getByTestId('recipe-step')
    .evaluateAll((items) => items.map((item) => item.getAttribute('data-processor') ?? ''));
}

/** The bar step of the n-th step of the recipe, counting from zero, which opens the step. */
export function barStepAt(page: Page, index: number): Locator {
  return page.getByTestId('bar-step').nth(index);
}

// An asset of a page version: `.../assets/pages/<page>/<stage>/<processor>/<version>/...`, the version being 16 hex digits
const VERSION_IN_PATH = /\/assets\/pages\/[^/]+\/[^/]+\/[^/]+\/([0-9a-f]{16})\//;

/** Read the identifier of the version an asset path belongs to, or null for a path of no version. */
export function versionIdOf(assetPath: string | null | undefined): string | null {
  return VERSION_IN_PATH.exec(assetPath ?? '')?.[1] ?? null;
}

/**
 * Wait until the canvas has settled on the picture of the open step: it is ready, and the first picture it loaded is the
 * version the strip shows for the open page, which is the one the open step reads. An editor drawn over the canvas is
 * mounted again whenever the canvas swaps pictures, so a drag started before this holds a shape the swap would cut off.
 */
export async function waitForStepPicture(page: Page, timeout: number): Promise<void> {
  const canvas = page.getByTestId('viewer-canvas');
  const thumbnail = page.locator('[data-testid="strip-page"][aria-pressed="true"] img');
  await expect(canvas).toHaveAttribute('data-state', 'ready', { timeout });
  await expect(async () => {
    const shown = versionIdOf(await thumbnail.getAttribute('src'));
    const drawn = versionIdOf((await canvas.getAttribute('data-sources'))?.split(' ')[0] ?? null);
    expect(shown).not.toBeNull();
    expect(drawn).toBe(shown);
  }).toPass({ timeout });
}
