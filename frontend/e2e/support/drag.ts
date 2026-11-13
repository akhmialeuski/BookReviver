import type { Page } from '@playwright/test';

/**
 * The keyboard drag of a sortable list in the scenarios. dnd-kit starts to listen for the arrow keys in a timer it sets
 * when Space lifts an item, after it has announced the lift, so an arrow pressed the moment the announcement is read is
 * lost, which a person never does.
 */

/** How long the page is given to set the key listeners of a drag after the lift. */
const KEY_LISTENER_DELAY_MS = 100;

/** Give the page the turn of its timers after a lift, so the arrow keys that follow move the lifted item. */
export async function waitForDragKeys(page: Page): Promise<void> {
  await page.evaluate(
    (ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
    KEY_LISTENER_DELAY_MS,
  );
}
