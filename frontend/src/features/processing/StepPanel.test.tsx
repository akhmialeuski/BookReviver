import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FigureState, StagePageSchema, StepPageSchema } from '@/api';
import type { EditorSession } from '@/features/editors/session';
import { SourceKind } from '@/features/processing/compare';
import {
  deskew,
  processing,
  processor,
  recipe,
  step,
  version,
} from '@/features/processing/fixtures';
import type { OrderIssue } from '@/features/processing/order';
import { StepPanel } from '@/features/processing/StepPanel';
import type { StageRun } from '@/features/processing/useStageRun';
import { page as pageOf, row, stepPage } from '@/features/workspace/fixtures';
import { barStepsOf, countStep } from '@/features/workspace/steps';
import { joinRows, type StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

const sdk = vi.hoisted(() => ({
  carry: vi.fn(),
  versions: vi.fn(),
  jobs: vi.fn(),
  settings: vi.fn(),
  put: vi.fn(),
  history: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPost: sdk.carry,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGet: sdk.settings,
  putSettingApiV1ProjectsProjectIdPagesPageIdSettingsStageStepIdNamePut: sdk.put,
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
}));

/**
 * The section of the panel for the open step: its settings, where it stands in the order, the state of its shape on the
 * open page and what the page changes for it, how the pages of the book stand at it and how many passed it, the run up to
 * it.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const SAVED = recipe('r1', {
  steps: [
    step('geometry.perspective', { step_id: 'a' }),
    step('geometry.deskew', { step_id: 'b', params: { max_angle: 5, min_confidence: 0.3 } }),
    step('geometry.deskew', { step_id: 'c' }),
  ],
});
const CATALOGUE = [processor('geometry.perspective', { title: 'Perspective' }), deskew()];
const BAR = barStepsOf(SAVED, CATALOGUE);
// Two pages of text and a plate, which the second Deskew, the one for pictures, goes over
const PAGES = joinRows(
  [
    pageOf('p1'),
    pageOf('p2'),
    pageOf('p3', { kind: 'plate' }),
    pageOf('p4', { origin: 'placeholder' }),
  ],
  [],
);

function editorStub(overrides: Partial<EditorSession> = {}): EditorSession {
  return {
    picture: { kind: SourceKind.Iiif, url: '/info.json' },
    alwaysOn: true,
    focused: true,
    figure: 'by-hand',
    active: true,
    steps: [],
    choose: vi.fn(),
    hasEdit: true,
    busy: false,
    error: null,
    open: vi.fn(),
    close: vi.fn(),
    auto: vi.fn(),
    reach: null,
    renderCanvas: () => null,
    renderPanel: () => <span data-testid="editor-own-part" />,
    ...overrides,
  };
}

function placed(state: FigureState, found: Record<string, unknown> = {}): StepPageSchema {
  return stepPage('b', state, {
    version: state === 'default' ? null : version('v', { data: found }),
    flags: state === 'by-hand' ? ['by-hand'] : [],
  });
}

describe('StepPanel', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const start = vi.fn();
  const change = vi.fn();
  const restoreOrder = vi.fn();

  function runStub(overrides: Partial<StageRun> = {}): StageRun {
    return {
      pageLabel: '',
      total: 0,
      choices: [],
      bodyOf: () => null,
      disabled: false,
      dirty: false,
      busy: false,
      pending: false,
      error: null,
      start,
      confirming: false,
      confirm: vi.fn(),
      cancel: vi.fn(),
      overwriting: null,
      confirmOverwrite: vi.fn(),
      cancelOverwrite: vi.fn(),
      ...overrides,
    };
  }

  function render(
    open = 1,
    page: StepPageSchema | null = placed('found', { angle: -1.4 }),
    run = runStub(),
    extra: {
      items?: readonly StripItem[];
      editor?: EditorSession | null;
      selected?: ReadonlySet<string>;
      rows?: StagePageSchema[];
      issues?: readonly OrderIssue[];
      /** The key of the processor the open step is shown as, for a step whose own panel parts are tested. */
      processorKey?: string;
      /** Whether the open step is switched off. */
      off?: boolean;
    } = {},
  ): void {
    const rows = extra.rows ?? [
      row('1', { step: placed('found') }),
      row('2', { step: placed('by-hand') }),
      row('3', { step: placed('skipped') }),
      row('4', { step: placed('default') }),
    ];
    const found = BAR[open];
    if (found === undefined) {
      throw new Error('No such step');
    }
    const opened = {
      ...found,
      ...(extra.processorKey === undefined ? {} : { processorKey: extra.processorKey }),
      ...(extra.off === true ? { enabled: false } : {}),
    };
    const workspace: StepWorkspace = {
      steps: BAR,
      open: opened,
      states: new Map(),
      page,
      counts: countStep(rows),
      rows,
    };
    const state = processing({
      catalogue: CATALOGUE,
      recipes: [SAVED],
      recipe: SAVED,
      steps: [
        {
          id: 'step-0',
          stepId: 'a',
          processorKey: 'geometry.perspective',
          params: {},
          enabled: true,
        },
        {
          id: 'step-1',
          stepId: 'b',
          processorKey: 'geometry.deskew',
          params: { max_angle: 5, min_confidence: 0.3 },
          enabled: true,
        },
        {
          id: 'step-2',
          stepId: 'c',
          processorKey: 'geometry.deskew',
          params: {},
          enabled: true,
        },
      ],
      orderIssues: new Map(extra.issues === undefined ? [] : [['step-1', extra.issues]]),
      restoreOrder,
      change,
    });
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <StepPanel
            processing={state}
            workspace={workspace}
            step={opened}
            pageLabel="14"
            pageId="p1"
            items={extra.items ?? PAGES}
            selected={extra.selected}
            editor={extra.editor ?? null}
            run={run}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  const text = (testId: string): string => find(testId)?.textContent ?? '';

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    for (const mock of [start, change, restoreOrder]) {
      mock.mockReset();
    }
    sdk.versions.mockReset();
    sdk.versions.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.settings.mockReset();
    sdk.settings.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.put.mockReset();
    sdk.put.mockResolvedValue({ data: {} });
    sdk.history.mockReset();
    sdk.history.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.jobs.mockReset();
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    sdk.carry.mockReset();
    sdk.carry.mockResolvedValue({ data: { batch_id: 'batch', changes: [], skipped: [] } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    for (const node of document.body.querySelectorAll('[role="menu"]')) {
      node.remove();
    }
    client.clear();
    vi.unstubAllGlobals();
  });

  it('names the open step by its number and its title', () => {
    render();

    expect(text('step-panel-title')).toBe('2 · Deskew');
    expect(find('step-panel')?.getAttribute('data-step-id')).toBe('b');
  });

  it('shows what the step found on the open page, and no angle for a page the step skipped', () => {
    render();
    expect(text('step-panel-angle')).toContain('-1.4°');

    render(1, placed('skipped', { skipped: true, angle: 0.5 }));
    expect(find('step-panel-angle')).toBeNull();
  });

  it('says a page that has not been through the step has not reached it', () => {
    render(1, stepPage('b', 'default'));

    expect(find('step-panel-not-reached')).not.toBeNull();
  });

  it('counts the pages of the book by what the step did on them', () => {
    render();

    expect(text('step-count-found')).toContain('1 page');
    expect(text('step-count-byHand')).toContain('1 page');
    expect(text('step-count-skipped')).toContain('1 page');
    expect(text('step-count-notRun')).toContain('1 page');
    expect(text('step-count-check')).toContain('0 pages');
  });

  it('offers the reset of the open step to its defaults, named by the step', () => {
    render(1);

    expect(find('reset-menu')?.getAttribute('aria-label')).toBe('Reset Deskew to the defaults');
  });

  it('leaves out the pages that differ from the book while none does, and counts them when some do', () => {
    render();
    expect(find('step-count-unusual')).toBeNull();

    render(1, undefined, undefined, {
      rows: [
        row('1', { step: stepPage('b', 'found', { flags: ['unusual'] }) }),
        row('2', { step: stepPage('b', 'found', { flags: ['unusual'] }) }),
        row('3', { step: stepPage('b', 'found', { flags: ['unsure'] }) }),
      ],
    });

    expect(text('step-count-unusual')).toContain('2 pages');
    expect(text('step-count-check')).toContain('1 page');
  });

  it('puts the controls of the page editor in the section of the page', () => {
    render(1, placed('by-hand', { angle: 0.5 }), runStub(), { editor: editorStub() });

    expect(
      find('step-panel-page')?.querySelector('[data-testid="editor-controls"]'),
    ).not.toBeNull();
    expect(
      find('step-panel-page')?.querySelector('[data-testid="editor-own-part"]'),
    ).not.toBeNull();
  });

  it('draws no controls of the editor for a step that has none or a page the step passed by', () => {
    render(1);

    expect(find('editor-controls')).toBeNull();
  });

  it('has no buttons to the steps either side, since the bar of the steps moves between them', () => {
    for (const index of [0, 1, 2]) {
      render(index);

      expect(find('step-previous')).toBeNull();
      expect(find('step-next')).toBeNull();
    }
  });

  it('has no button that closes the step, since a stage with a bar always has one open', () => {
    render();

    expect(find('step-close')).toBeNull();
  });

  it('draws the settings of the step with ids of their own, so the form of the page can stand beside it', () => {
    render();

    expect(container.querySelector('[id^="step-panel_"]')).not.toBeNull();
    expect(container.querySelector('[id^="root_"]')).toBeNull();
  });

  describe('the pages that passed the step', () => {
    const runOver = (count: number): StageRun =>
      runStub({
        total: count,
      });
    const passedItems = (): StripItem[] =>
      joinRows(
        [pageOf('p1'), pageOf('p2'), pageOf('p3')],
        [
          row('p1', { recipe_id: 'r1', status: 'fresh', through_step: null }),
          row('p2', { recipe_id: 'r1', status: 'stale', through_step: 0 }),
          row('p3', { recipe_id: 'r1', status: 'not-run' }),
        ],
      );

    it('says how many pages of the book passed it, counting those a run stopped before it out', () => {
      render(1, placed('found'), runOver(3), { items: passedItems() });

      expect(text('step-passed')).toBe('1 of 3 pages passed');
    });

    it('says nothing of the pages for a step that is off', () => {
      render(1, placed('found'), runOver(3), { items: passedItems(), off: true });

      expect(find('step-passed')).toBeNull();
    });
  });

  describe('the order of the step', () => {
    const REASON =
      'Deskew reads the slant of the lines on an upright sheet, so it usually comes after Perspective.';
    const REQUIRED_REASON = 'Margins cannot come before Select content.';
    const issue = (kind: 'usual' | 'required', reason: string): OrderIssue => ({
      stepId: 'step-1',
      otherId: 'step-0',
      kind,
      reason,
    });

    it('gives every reason it stands off its place, with the button that restores the usual order', () => {
      render(1, placed('found'), runStub(), {
        issues: [issue('usual', REASON), issue('required', REQUIRED_REASON)],
      });

      expect(text('step-order-details')).toContain(REASON);
      expect(text('step-order-details')).toContain(REQUIRED_REASON);
      expect(find('step-order-details')?.dataset.kind).toBe('required');
      act(() => find('step-restore-order')?.click());
      expect(restoreOrder).toHaveBeenCalledTimes(1);
    });

    it('draws nothing of the order for a step that stands in its place', () => {
      render(1);

      expect(find('step-order-details')).toBeNull();
      expect(find('step-restore-order')).toBeNull();
    });
  });

  describe('what the open page changes for the step', () => {
    const settled = (): Promise<void> =>
      act(async () => {
        await vi.waitFor(() => expect(find('page-settings-list')).not.toBeNull());
      });

    beforeEach(() => {
      sdk.settings.mockResolvedValue({
        data: {
          items: [
            {
              page_id: 'p1',
              stage: 'geometry',
              step_id: 'b',
              params: { min_confidence: 0.6 },
              updated_at: '',
            },
          ],
          total: 1,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
    });

    it('lists the fields the page changes with the way back, and leaves the history to the end of the stage panel', async () => {
      render(1);
      await settled();

      expect(text('page-settings-list')).toContain('Least confidence');
      expect(find('page-settings-reset')).not.toBeNull();
      expect(find('page-history')).toBeNull();
      expect(sdk.history).not.toHaveBeenCalled();
    });

    it('marks the fields the page changes in the form of the step', async () => {
      render(1);
      await settled();

      const labels = [
        ...container.querySelectorAll('[data-testid="step-panel-settings"] form label'),
      ].map((label) => label.textContent);
      expect(labels).toEqual(['Largest slant', 'Least confidence · changed for this page']);
    });

    it('offers the reset of the step once, which the section of the page shares', async () => {
      render(1);
      await settled();

      expect(container.querySelectorAll('[data-testid="reset-menu"]')).toHaveLength(1);
    });

    it('sets nothing for the page when the form of a step is opened after another step was open', async () => {
      render(0);
      render(1);
      await settled();

      await act(async () => find('page-settings-edit')?.click());

      // The form starts from what the page runs with at this step, so none of its fields counts as typed into
      expect(sdk.put).not.toHaveBeenCalled();
    });
  });

  describe('the measure of the book', () => {
    it('stands in the settings of the step that places the block, and in no other', () => {
      render(1, placed('found'), runStub(), { processorKey: 'geometry.normalize' });
      expect(find('measure-book')).not.toBeNull();

      render(1);
      expect(find('measure-book')).toBeNull();
    });
  });

  describe('carrying the shape over', () => {
    async function choose(id: string): Promise<void> {
      const trigger = find('carry-menu');
      await act(async () => {
        trigger?.focus();
        trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      });
      await act(async () => {
        document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      });
    }

    it('offers the carry-over only for a shape the reader set by hand', () => {
      render(1, placed('by-hand', { angle: 0.5 }));
      expect(find('step-carry')).not.toBeNull();

      for (const state of ['found', 'default', 'skipped'] as const) {
        render(1, placed(state));
        expect(find('step-carry')).toBeNull();
      }
    });

    it('carries the shape of the step to the following pages, naming no field', async () => {
      render(1, placed('by-hand', { angle: 0.5 }));
      await choose('carry-following');

      expect(sdk.carry).toHaveBeenCalledTimes(1);
      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p1', stage: 'geometry', step_id: 'b' },
        body: { scope: 'following', overwrite: false },
      });
    });

    it('carries it to every page of the same kind, over the pages with a shape of their own when asked', async () => {
      render(1, placed('by-hand', { angle: 0.5 }));
      act(() => find('step-carry-overwrite')?.click());
      await choose('carry-kind');

      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        body: { scope: 'kind', overwrite: true },
      });
    });

    it('carries it to the pages selected in the grid, but the open one', async () => {
      render(1, placed('by-hand', { angle: 0.5 }), runStub(), { selected: new Set(['p1', 'p2']) });
      await choose('carry-selected');

      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        body: { scope: 'selected', page_ids: ['p2'] },
      });
    });
  });

  describe('the results of the step on the open page', () => {
    it('leaves the results and the changes to the history at the end of the stage panel, and reads none', async () => {
      sdk.versions.mockResolvedValue({
        data: {
          items: [version('old', { created_at: '2026-09-30T10:00:00Z' }), version('v')],
          total: 2,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
      render(1);
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(find('results')).toBeNull();
      expect(find('page-history-row')).toBeNull();
      expect(find('page-history-use')).toBeNull();
      expect(find('page-history')).toBeNull();
      expect(sdk.versions).not.toHaveBeenCalled();
      expect(sdk.history).not.toHaveBeenCalled();
    });
  });
});
