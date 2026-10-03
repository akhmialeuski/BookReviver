import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FigureState, StepPageSchema } from '@/api';
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
import { row } from '@/features/workspace/fixtures';
import { barStepsOf, countStep, neighboursOf } from '@/features/workspace/steps';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

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
  const onOpen = vi.fn();
  const onClose = vi.fn();
  const start = vi.fn();
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
      confirming: false,
      confirm: vi.fn(),
      cancel: vi.fn(),
      ...overrides,
    };
  }

  function render(
    open = 1,
    page: StepPageSchema | null = placed('found', { angle: -1.4 }),
    run = runStub(),
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
        <StepPanel
          processing={state}
          workspace={workspace}
          step={opened}
          pageLabel="14"
          run={run}
          onOpen={onOpen}
          onClose={onClose}
        />,
      ),
    );
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  const text = (testId: string): string => find(testId)?.textContent ?? '';

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    for (const mock of [onOpen, onClose, start, condition, change]) {
      mock.mockReset();
    }
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
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
});
