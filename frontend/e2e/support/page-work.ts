import { expect, type Locator, type Page } from '@playwright/test';
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from '../../src/shared/http/csrf';
import { openProjectId, waitForIdleJobs } from './account';

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

/** Set the value a page uses for the field of the step, as a value for the open page does. */
export async function putSetting(
  page: Page,
  pageId: string,
  stepId: string,
  value: number,
): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/stages/geometry/steps/${stepId}/values/${FIELD}`,
    { headers: await changeHeaders(page), data: { scope: 'pages', page_ids: [pageId], value } },
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

/** Read the fields a page changes for itself in the steps of the stage, by name, one entry for each step that has any. */
export async function readSettings(page: Page, pageId: string): Promise<Record<string, number>[]> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry`,
  );
  const body = (await response.json()) as { items: { params: Record<string, number> }[] };
  return body.items.map((item) => item.params);
}

/** Read the value of the field the step runs with on a page, which the part of the book it is in may give it. */
export async function readEffective(page: Page, pageId: string): Promise<number | undefined> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry`,
  );
  const body = (await response.json()) as { items: { effective: Record<string, number> }[] };
  return body.items[0]?.effective[FIELD];
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
  // The chip with the number of events stands once the section has read what it holds for the open page and step, so the
  // panel above it has stopped moving and the click lands on the header
  await expect(history.getByTestId('page-history-count')).toBeVisible();
  await expect(toggle).toBeEnabled();
  if ((await toggle.getAttribute('data-state')) !== 'open') {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute('data-state', 'open');
  return history;
}

/** Count the runs of a stage that ended well in the open book, which tells that a run the reader started is over. */
export async function finishedRuns(page: Page): Promise<number> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=50`);
  const items = ((await listed.json()) as { items: { kind: string; state: string }[] }).items;
  return items.filter((job) => job.kind === 'run-stage' && job.state === 'succeeded').length;
}

/** Read the identifiers of the newest runs of a stage that ended well in the open book. */
async function endedRunIds(page: Page): Promise<Set<string>> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=100`);
  const items = ((await listed.json()) as { items: { id: string; kind: string; state: string }[] })
    .items;
  return new Set(
    items
      .filter((job) => job.kind === 'run-stage' && job.state === 'succeeded')
      .map((job) => job.id),
  );
}

/** How long a run on all pages may take before the scenario gives up on it. */
const RUN_ALL_TIMEOUT_MS = 120_000;

/**
 * Run the stage on all pages from the menu of the run, and wait until that run has ended and the book is idle.
 *
 * The summary of the stage reads "up to date" before a run of pages that are up to date has begun, and while the run still
 * places the pages, so the end of the job is what is waited for, and then for whatever the book queued after it.
 *
 * @param page The page of the browser, on a stage with a run menu.
 */
export async function runAllPages(page: Page): Promise<void> {
  // A run an edit started is over first, so it is not counted as the one asked for here
  await waitForIdleJobs(page, openProjectId(page));
  const known = await endedRunIds(page);
  await page.getByTestId('run-menu').click();
  await page.getByTestId('run-pages-all').click();
  await page.keyboard.press('Escape');
  await page.getByTestId('run-start').click();
  // The jobs listed are the newest ones, so a scenario with many runs is told by the identifiers and not by their number
  await expect
    .poll(async () => [...(await endedRunIds(page))].some((id) => !known.has(id)), {
      timeout: RUN_ALL_TIMEOUT_MS,
    })
    .toBe(true);
  await waitForIdleJobs(page, openProjectId(page));
}
