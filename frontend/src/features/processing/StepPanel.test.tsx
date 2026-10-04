import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FigureState, StepPageSchema } from '@/api';
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
import { StepPanel } from '@/features/processing/StepPanel';
import type { StageRun } from '@/features/processing/useStageRun';
import { page as pageOf, row } from '@/features/workspace/fixtures';
import { barStepsOf, countStep, neighboursOf } from '@/features/workspace/steps';
import { joinRows, type StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

const sdk = vi.hoisted(() => ({ carry: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPost: sdk.carry,
}));

/**
 * The section of the panel for the open step: its settings, the state of its shape on the open page, how the pages of the
 * book stand at it, the run up to it on every page, and the way to the steps either side.
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
    step('geometry.deskew', { step_id: 'c', applies_to: 'pictures' }),
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
    renderCanvas: () => null,
    renderPanel: () => <span data-testid="editor-own-part" />,
    ...overrides,
  };
}

function placed(state: FigureState, found: Record<string, unknown> = {}): StepPageSchema {
  return {
    step_id: 'b',
    state,
    input_version: null,
    version: state === 'default' ? null : version('v', { data: found }),
  };
}

describe('StepPanel', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const onOpen = vi.fn();
  const onClose = vi.fn();
  const start = vi.fn();
  const startPages = vi.fn();
  const condition = vi.fn();
  const change = vi.fn();

  function runStub(overrides: Partial<StageRun> = {}): StageRun {
    return {
      choices: [],
      describe: () => '',
      disabled: false,
      dirty: false,
      busy: false,
      pending: false,
      error: null,
      start,
      startPages,
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
    } = {},
  ): void {
    const rows = [
      row('1', { step: placed('found') }),
      row('2', { step: placed('by-hand') }),
      row('3', { step: placed('skipped') }),
      row('4', { step: placed('default') }),
    ];
    const opened = BAR[open];
    if (opened === undefined) {
      throw new Error('No such step');
    }
    const workspace: StepWorkspace = {
      steps: BAR,
      open: opened,
      states: new Map(),
      page,
      counts: countStep(rows, opened.processorKey),
      neighbours: neighboursOf(BAR, opened),
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
          appliesTo: 'all',
        },
        {
          id: 'step-1',
          stepId: 'b',
          processorKey: 'geometry.deskew',
          params: { max_angle: 5, min_confidence: 0.3 },
          enabled: true,
          appliesTo: 'all',
        },
        {
          id: 'step-2',
          stepId: 'c',
          processorKey: 'geometry.deskew',
          params: {},
          enabled: true,
          appliesTo: 'pictures',
        },
      ],
      condition,
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
            onOpen={onOpen}
            onClose={onClose}
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
    for (const mock of [onOpen, onClose, start, startPages, condition, change]) {
      mock.mockReset();
    }
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

  it('shows the state of the shape on the open page and what the step found', () => {
    render();

    expect(text('step-panel-state')).toContain('Found by the step');
    expect(text('step-panel-angle')).toContain('-1.4°');
  });

  it('says a shape was set by hand, and shows no angle for a page the step skipped', () => {
    render(1, placed('by-hand', { angle: 0.5 }));
    expect(text('step-panel-state')).toContain('Set by hand');

    render(1, placed('skipped', { skipped: true, angle: 0.5 }));
    expect(text('step-panel-state')).toContain('Skipped on this page');
    expect(find('step-panel-angle')).toBeNull();
  });

  it('says a page that has not been through the step holds the default shape', () => {
    render(1, { step_id: 'b', state: 'default', input_version: null, version: null });

    expect(text('step-panel-state')).toContain('Default shape');
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

  it('runs the recipe up to the step on every page', () => {
    render(1);
    act(() => find('step-auto')?.click());

    expect(start).toHaveBeenCalledWith('all', 1);
  });

  it('runs the recipe up to the step on the open page alone', () => {
    render(1);
    act(() => find('step-auto-page')?.click());

    expect(startPages).toHaveBeenCalledWith(['p1'], 1);
  });

  it('offers a run on the pages of the condition only to a step that has one, over the pages it may meet', () => {
    render(1);
    expect(find('step-auto-condition')).toBeNull();

    render(2);
    expect(text('step-auto-condition')).toContain('1 page');
    act(() => find('step-auto-condition')?.click());

    // The plate, and not the pages of text, and not the placeholder, which has no image to process
    expect(startPages).toHaveBeenCalledWith(['p3'], 2);
  });

  it('puts the controls of the page editor in the section of the page, and says what the state of the shape means', () => {
    render(1, placed('by-hand', { angle: 0.5 }), runStub(), { editor: editorStub() });

    expect(
      find('step-panel-page')?.querySelector('[data-testid="editor-controls"]'),
    ).not.toBeNull();
    expect(
      find('step-panel-page')?.querySelector('[data-testid="editor-own-part"]'),
    ).not.toBeNull();
    expect(text('step-panel-hint')).toContain('Orange');

    render(1, placed('default'));
    expect(text('step-panel-hint')).toContain('grey');
  });

  it('draws no controls of the editor for a step that has none or a page the step passed by', () => {
    render(1);

    expect(find('editor-controls')).toBeNull();
  });

  it('waits for the recipe to be saved before it runs, and says so', () => {
    render(1, placed('found'), runStub({ disabled: true, dirty: true }));

    expect(find('step-auto')?.hasAttribute('disabled')).toBe(true);
    expect(container.textContent).toContain('Save the recipe to run it.');
  });

  it('moves to the steps either side, and offers none past the first and the last', () => {
    render(1);
    act(() => find('step-previous')?.click());
    act(() => find('step-next')?.click());

    expect(onOpen.mock.calls).toEqual([['a'], ['c']]);

    render(0);
    expect(find('step-previous')).toBeNull();
    render(2);
    expect(find('step-next')).toBeNull();
  });

  it('closes the step', () => {
    render();
    act(() => find('step-close')?.click());

    expect(onClose).toHaveBeenCalledOnce();
  });

  it('changes the condition of the step in the draft of the recipe', () => {
    render();
    const select = find('step-panel-condition') as HTMLSelectElement;
    act(() => {
      select.value = 'text';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(condition).toHaveBeenCalledWith('step-1', 'text');
  });

  it('draws the settings of the step with ids of their own, so the list below can show the same step', () => {
    render();

    expect(container.querySelector('[id^="step-panel_"]')).not.toBeNull();
    expect(container.querySelector('[id^="root_"]')).toBeNull();
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

    it('carries it to every page of the condition, over the pages with a shape of their own when asked', async () => {
      render(1, placed('by-hand', { angle: 0.5 }));
      act(() => find('step-carry-overwrite')?.click());
      await choose('carry-condition');

      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        body: { scope: 'condition', overwrite: true },
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
});
