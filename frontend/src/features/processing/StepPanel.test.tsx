import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FigureState, StagePageSchema, StepPageSchema } from '@/api';
import type { EditorSession } from '@/features/editors/session';
import { SourceKind } from '@/features/processing/compare';
import {
  deskew,
  pageValues,
  processing,
  processor,
  recipe,
  step,
  stepSettings,
  version,
} from '@/features/processing/fixtures';
import type { OrderIssue } from '@/features/processing/order';
import type { PageValues } from '@/features/processing/pageSettings';
import { StepPanel } from '@/features/processing/StepPanel';
import { row, stepPage } from '@/features/workspace/fixtures';
import { barStepsOf, countStep } from '@/features/workspace/steps';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

const sdk = vi.hoisted(() => ({
  carry: vi.fn(),
  versions: vi.fn(),
  jobs: vi.fn(),
  settings: vi.fn(),
  history: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPost: sdk.carry,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGet: sdk.settings,
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
}));

/**
 * The section of the panel for the open step: its settings, with the values the open page and the parts of the pages have
 * for each, where it stands in the order, and the state of its shape on the open page.
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

  function render(
    open = 1,
    page: StepPageSchema | null = placed('found', { angle: -1.4 }),
    extra: {
      values?: PageValues;
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
            values={extra.values}
            selected={extra.selected}
            editor={extra.editor ?? null}
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

  it('puts the controls of the page editor in the section of the page', () => {
    render(1, placed('by-hand', { angle: 0.5 }), { editor: editorStub() });

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

  it('has no Auto button, whatever the state of the shape, since Auto stands on the toolbar of the canvas', () => {
    for (const state of ['default', 'found', 'by-hand', 'skipped'] as const) {
      render(1, placed(state, { angle: 0.5 }), { editor: editorStub({ figure: state }) });

      const names = [...container.querySelectorAll('button')].map((button) =>
        (button.textContent ?? '').trim(),
      );
      expect(names.some((name) => /^Auto\b/.test(name))).toBe(false);
      expect(container.querySelector('[data-testid^="step-auto"]')).toBeNull();
      expect(container.querySelector('[data-testid^="step-run-"]')).toBeNull();
      expect(find('editor-auto')).toBeNull();
    }
  });

  describe('the section of the open page', () => {
    it('has no line for the state of the shape, no legend of its colours and no card of hints', () => {
      for (const state of ['default', 'found', 'by-hand', 'skipped'] as const) {
        render(1, placed(state, { angle: 0.5 }), { editor: editorStub({ figure: state }) });

        const section = find('step-panel-page');
        expect(find('step-panel-state')).toBeNull();
        expect(find('step-panel-hint')).toBeNull();
        expect(section?.querySelector('[data-testid$="-hint"]')).toBeNull();
        expect(section?.textContent).not.toMatch(/orange|grey|green/i);
        expect(section?.textContent).not.toMatch(/\bShape\b/);
      }
    });
  });

  it('draws the settings of the step with ids of their own, so the form of the page can stand beside it', () => {
    render();

    expect(container.querySelector('[id^="step-panel_"]')).not.toBeNull();
    expect(container.querySelector('[id^="root_"]')).toBeNull();
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
      render(1, placed('found'), {
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

  describe('the values for parts of the pages', () => {
    const VALUES = pageValues({
      settings: [
        stepSettings('b', {
          params: { min_confidence: 0.6 },
          parts: [
            {
              scope: 'even',
              group_label: '',
              params: { max_angle: 3 },
              updated_at: '2026-10-01T00:00:00Z',
            },
          ],
        }),
      ],
    });

    it('draws the values of each setting under it, and nothing else of the page in the settings', () => {
      render(1, undefined, { values: VALUES });

      const chips = [...container.querySelectorAll('[data-testid="value-chip"]')].map(
        (chip) => chip.textContent,
      );
      expect(chips).toEqual(['Even pages 3', 'p. 143 0.6']);
      expect(find('step-panel-settings')?.contains(find('field-values'))).toBe(true);
    });

    it('draws no values when no page is open', () => {
      render(1);

      expect(find('field-values')).toBeNull();
    });

    it('has no block of the changes of the page, no reset menu and no statistics of the step on the book', () => {
      render(1, undefined, { values: VALUES });

      for (const gone of [
        'page-settings',
        'page-settings-edit',
        'reset-menu',
        'step-panel-book',
        'step-passed',
        'step-count-found',
      ]) {
        expect(find(gone)).toBeNull();
      }
      expect(sdk.history).not.toHaveBeenCalled();
    });
  });

  describe('the measure of the book', () => {
    it('stands in the settings of the step that places the block, and in no other', () => {
      render(1, placed('found'), { processorKey: 'geometry.normalize' });
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
      render(1, placed('by-hand', { angle: 0.5 }), { selected: new Set(['p1', 'p2']) });
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
