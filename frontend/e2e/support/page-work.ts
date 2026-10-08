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
export async function changeHeaders(page: Page): Promise<Record<string, string>> {
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

/** The part of the pages a value of a setting is for, as the form of the API names it. */
type ValueTarget = { scope: 'pages'; page_ids: string[] } | { scope: 'odd' | 'even' };

/** Send the value the pages of a target use for a field of a step, as the form of a step does. */
async function putValue(
  page: Page,
  stepId: string,
  target: ValueTarget,
  value: number,
  field: string,
): Promise<void> {
  const response = await page.request.put(
    `/api/v1/projects/${openProjectId(page)}/stages/geometry/steps/${stepId}/values/${field}`,
    { headers: await changeHeaders(page), data: { ...target, value } },
  );
  expect(response.ok()).toBe(true);
}

/**
 * Set the value a page uses for the field of the step, as a value for the open page does. The page then has work of its
 * own, which a run that leaves such pages out passes by.
 */
export async function putSetting(
  page: Page,
  pageId: string,
  stepId: string,
  value: number,
  field = FIELD,
): Promise<void> {
  await putValue(page, stepId, { scope: 'pages', page_ids: [pageId] }, value, field);
}

/**
 * Set the value the odd pages or the even pages use for the field of the step. A page of that side is then out of date,
 * and has no work of its own, since the value belongs to the side.
 */
export async function putSideValue(
  page: Page,
  stepId: string,
  side: 'odd' | 'even',
  value: number,
  field = FIELD,
): Promise<void> {
  await putValue(page, stepId, { scope: side }, value, field);
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

/**
 * Read the value of the field a step runs with on a page, which the part of the book it is in may give it.
 *
 * @param options `stepId` names the step, and the first step that has values is read when it is left out, and `field`
 * names the field, which is the one of the Deskew step unless the scenario is about another.
 */
export async function readEffective(
  page: Page,
  pageId: string,
  { stepId, field = FIELD }: { stepId?: string; field?: string } = {},
): Promise<number | undefined> {
  const response = await page.request.get(
    `/api/v1/projects/${openProjectId(page)}/pages/${pageId}/settings/geometry`,
  );
  const body = (await response.json()) as {
    items: { step_id: string; effective: Record<string, number> }[];
  };
  const item =
    stepId === undefined ? body.items[0] : body.items.find((one) => one.step_id === stepId);
  return item?.effective[field];
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

/** Read the jobs of the open book that are runs of a stage, whatever their state, newest first. */
async function runJobs(page: Page): Promise<{ id: string; state: string }[]> {
  const listed = await page.request.get(`/api/v1/projects/${openProjectId(page)}/jobs?size=100`);
  const items = ((await listed.json()) as { items: { id: string; kind: string; state: string }[] })
    .items;
  return items.filter((job) => job.kind === 'run-stage');
}

/** Count the runs of a stage the open book has had, so a scenario can tell that none was started. */
export async function countRunJobs(page: Page): Promise<number> {
  return (await runJobs(page)).length;
}

/** Read the identifiers of the newest runs of a stage that ended well in the open book. */
async function endedRunIds(page: Page): Promise<Set<string>> {
  return new Set(
    (await runJobs(page)).filter((job) => job.state === 'succeeded').map((job) => job.id),
  );
}

/** How long a run of the stage may take before the scenario gives up on it. */
const RUN_TIMEOUT_MS = 120_000;

/** The pages the menu of the run can choose, as the end of the test id of each choice spells them. */
export type RunPages = 'page' | 'from-page' | 'selected' | 'group:text' | 'attention' | 'all';

/**
 * Run the stage from the menu of the run on the pages it names, and wait until that run has ended and the book is idle.
 *
 * The summary of the stage reads "up to date" before a run of pages that are up to date has begun, and while the run still
 * places the pages, so the end of the job is what is waited for, and then for whatever the book queued after it.
 *
 * @param page The page of the browser, on a stage with a run menu.
 * @param options `pages` chooses the pages and is all of them unless it says otherwise, `throughOpenStep` stops the run
 * at the step open in the bar instead of going through the last step, and `ownWork` chooses what the run does with the
 * pages that have work of their own, which the menu offers only when some of the pages chosen have it. `button` is what
 * the button of the run must say once the choices are made, before it is pressed. A run that drops that work asks for a
 * confirmation first, so a scenario about it clicks the menu itself.
 */
export async function runPages(
  page: Page,
  {
    pages = 'all',
    throughOpenStep = false,
    ownWork,
    button,
  }: {
    pages?: RunPages;
    throughOpenStep?: boolean;
    ownWork?: 'keep' | 'skip-own-work';
    button?: string;
  } = {},
): Promise<void> {
  // A run an edit started is over first, so it is not counted as the one asked for here
  await waitForIdleJobs(page, openProjectId(page));
  const known = await endedRunIds(page);
  await page.getByTestId('run-menu').click();
  await page.getByTestId(`run-pages-${pages}`).click();
  // The choice of how far the run goes stays as it was left, and is offered while a step other than the first is open
  const through = page.getByTestId(throughOpenStep ? 'run-through-open' : 'run-through-stage');
  if (throughOpenStep || (await through.count()) > 0) {
    await through.click();
  }
  if (ownWork !== undefined) {
    await page.getByTestId(`run-own-${ownWork}`).click();
  }
  await page.keyboard.press('Escape');
  if (button !== undefined) {
    await expect(page.getByTestId('run-start')).toContainText(button);
  }
  await page.getByTestId('run-start').click();
  // The jobs listed are the newest ones, so a scenario with many runs is told by the identifiers and not by their number
  await expect
    .poll(async () => [...(await endedRunIds(page))].some((id) => !known.has(id)), {
      timeout: RUN_TIMEOUT_MS,
    })
    .toBe(true);
  await waitForIdleJobs(page, openProjectId(page));
}

/** Where a page stands in a stage and which of its versions is the result, as the rows of the stage give them. */
export interface StageRow {
  status: string;
  /** The kind of recipe that processes the page. */
  kind: string;
  /** The identifier of the version that is the result, or null while there is none. */
  version: string | null;
}

/** Read the rows of a stage in the order of the book, which say what each page is and whether its result is up to date. */
export async function readStageRows(page: Page, stage = 'geometry'): Promise<StageRow[]> {
  const projectId = openProjectId(page);
  const listed = await page.request.get(
    `/api/v1/projects/${projectId}/stages/${stage}/pages?size=100`,
  );
  const items = (
    (await listed.json()) as {
      items: { page_id: string; status: string; kind: string; version: { id: string } | null }[];
    }
  ).items;
  const rows = new Map(
    items.map((item) => [
      item.page_id,
      { status: item.status, kind: item.kind, version: item.version?.id ?? null },
    ]),
  );
  return (await pageIds(page)).map((id) => {
    const row = rows.get(id);
    if (row === undefined) {
      throw new Error(`The stage ${stage} has no row for the page ${id}.`);
    }
    return row;
  });
}

/** Tell which pages of a stage a run changed, by their places from zero: those whose state or result is not what it was. */
export function changedPages(before: readonly StageRow[], after: readonly StageRow[]): number[] {
  return after.flatMap((row, position) => {
    const was = before[position];
    return was?.status === row.status && was.version === row.version ? [] : [position];
  });
}

/**
 * Select pages of the strip by their places in the book and come back to the strip, which keeps the selection.
 *
 * @param positions The places from zero of at least two pages, the first of which is chosen by a click and the others
 * added to it.
 */
export async function selectPagesInGrid(page: Page, positions: readonly number[]): Promise<void> {
  await page.getByTestId('strip-view-switch').click();
  await expect(page).toHaveURL(/view=grid/);
  const tiles = page.getByTestId('strip-page');
  for (const [order, position] of positions.entries()) {
    await tiles.nth(position).click({ modifiers: order === 0 ? [] : ['ControlOrMeta'] });
  }
  await expect(page.getByTestId('grid-selection')).toHaveText(`${positions.length} pages selected`);
  await page.getByTestId('strip-view-switch').click();
  await expect(page).not.toHaveURL(/view=grid/);
}
