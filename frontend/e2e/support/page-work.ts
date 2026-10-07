import { expect, type Locator, type Page } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../../src/shared/http/csrf';
import { openProjectId } from './account';

/**
 * What a scenario sets up and reads back through the API for the work of the pages of the Geometry stage: the settings a
 * page changes for a step, the manual edits, and the history of a step on a page.
 */

const FIELD = 'max_angle';
const ANGLE_OF_THE_EDIT = 1.5;

/** The rows of the history that are changes of a step, which share their test id with the rows that are results. */
export const CHANGE_ROWS = '[data-testid="page-history-row"][data-kind="change"]';

/** The rows of the history that are results of a step, which share their test id with the rows that are changes. */
export const RESULT_ROWS = '[data-testid="page-history-row"][data-kind="result"]';

/** One change of the history of a step on a page, as the API lists it. */
export interface HistoryItem {
  layer: string;
  source: string;
  undone: boolean;
}

/** The headers a change made straight through the API carries, as the screen sends them. */
async function changeHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  return {
    [CSRF_HEADER_NAME]: cookies.find((cookie) => cookie.name === CSRF_COOKIE_NAME)?.value ?? '',
  };
}

/** Read the identifiers of the pages of the open book in the order of the book. */
export async function pageIds(page: Page): Promise<string[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/pages?size=100`);
  const items = ((await listed.json()) as { items: { id: string; position: number }[] }).items;
  return items.sort((a, b) => a.position - b.position).map((item) => item.id);
}

/** Set the value a page uses for the field of the step, as the settings of a page do. */
export async function putSetting(
  page: Page,
  pageId: string,
  stepId: string,
  value: number,
): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry/${stepId}/${FIELD}`,
    { headers: await changeHeaders(page), data: { value } },
  );
  expect(response.ok()).toBe(true);
}

/** Save the rotation edit of a step as the editor does. */
export async function saveRotation(page: Page, pageId: string, stepId: string): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/edits/geometry/${stepId}`,
    {
      headers: await changeHeaders(page),
      multipart: { kind: 'rotation', geometry: JSON.stringify({ degrees: ANGLE_OF_THE_EDIT }) },
    },
  );
  expect(response.ok()).toBe(true);
}

/** Read the fields a page changes for the steps of the stage, by name, one entry for each step that has any. */
export async function readSettings(page: Page, pageId: string): Promise<Record<string, number>[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry`,
  );
  const body = (await response.json()) as { items: { params: Record<string, number> }[] };
  return body.items.map((item) => item.params);
}

/** Count the manual edits a page has in the stage. */
export async function countEdits(page: Page, pageId: string): Promise<number> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/edits/geometry`,
  );
  return ((await response.json()) as { total: number }).total;
}

/** Read the history of a step on a page from the server, the newest change first. */
export async function readHistory(
  page: Page,
  pageId: string,
  stepId: string,
): Promise<HistoryItem[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/history/geometry/${stepId}?size=100`,
  );
  return ((await response.json()) as { items: HistoryItem[] }).items;
}

/**
 * Open the history of the page at the end of the panel of the stage, which is collapsed until the reader opens it and
 * then stays as the reader left it on every stage.
 *
 * @param page The page of the browser, on a stage with a page open.
 * @returns The section, open.
 */
export async function openTimeline(page: Page): Promise<Locator> {
  const history = page.getByTestId('stage-panel').getByTestId('page-history');
  const toggle = history.getByTestId('page-history-toggle');
  await expect(toggle).toBeEnabled();
  if ((await toggle.getAttribute('data-state')) !== 'open') {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute('data-state', 'open');
  return history;
}
