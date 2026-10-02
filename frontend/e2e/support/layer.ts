import type { Locator, Page } from '@playwright/test';

/**
 * Reading and moving the shapes of a page editor in the scenarios: the layer of the editor writes where its shapes and
 * handles stand into attributes, and a drag starts from the place a handle is at.
 */

const DRAG_STEPS = 8;

/** Read a list of numbers an editor writes into an attribute of its layer, such as `10,20` or `1,2,3,4`. */
export async function numbersOf(layer: Locator, attribute: string): Promise<number[]> {
  const text = (await layer.getAttribute(attribute)) ?? '';
  const numbers = text.split(',').map(Number);
  if (numbers.length === 0 || numbers.some((value) => Number.isNaN(value))) {
    throw new Error(`The attribute ${attribute} holds "${text}", which is not a list of numbers.`);
  }
  return numbers;
}

/** Read a pair of numbers an editor writes into the attribute of its layer, such as the place of a handle. */
export async function pairOf(layer: Locator, attribute: string): Promise<{ x: number; y: number }> {
  const [x, y] = await numbersOf(layer, attribute);
  if (x === undefined || y === undefined) {
    throw new Error(`The attribute ${attribute} does not hold a pair of numbers.`);
  }
  return { x, y };
}

/** Drag from a place of the layer by an offset, in small steps so that the shape follows. */
export async function dragFrom(
  page: Page,
  layer: Locator,
  from: { x: number; y: number },
  by: { x: number; y: number },
): Promise<void> {
  const box = await layer.boundingBox();
  if (box === null) {
    throw new Error('The editor layer has no box.');
  }
  await page.mouse.move(box.x + from.x, box.y + from.y);
  await page.mouse.down();
  await page.mouse.move(box.x + from.x + by.x, box.y + from.y + by.y, { steps: DRAG_STEPS });
  await page.mouse.up();
}
