import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processing, recipe, version } from '@/features/processing/fixtures';
import { ProcessingPanel } from '@/features/processing/ProcessingPanel';
import { page, row, stepPage } from '@/features/workspace/fixtures';
import { barStepsOf, countStep, neighboursOf } from '@/features/workspace/steps';
import { joinRows } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

/**
 * Where the panel of a stage puts the history of the page: once, as the last element of the scrolling area of the panel
 * after every other section, whatever the state of the panel, with the step open in the bar or, on a stage without a bar,
 * the step open in the list of the recipe, and with no step when none is open.
 *
 * The sections are replaced by stand-ins that only name themselves, so the test reads their order and what the history
 * was given, and nothing reaches the server.
 */

const { stub } = vi.hoisted(() => ({
  stub: (testId: string) => () => <div data-testid={testId} />,
}));

vi.mock('@/features/processing/StepPanel', () => ({ StepPanel: stub('step-panel') }));
vi.mock('@/features/processing/SplitSection', () => ({ SplitSection: stub('split-section') }));
vi.mock('@/features/processing/RecipeSection', () => ({ RecipeSection: stub('recipe-section') }));
vi.mock('@/features/processing/ContentTypeSection', () => ({
  ContentTypeSection: stub('content-type'),
}));
vi.mock('@/features/processing/ThisPageSection', () => ({ ThisPageSection: stub('this-page') }));
vi.mock('@/features/processing/RunControls', () => ({ RunControls: stub('run-controls') }));
vi.mock('@/features/profiles/ProfileLibraryPanel', () => ({ ProfileLibraryPanel: () => null }));
vi.mock('@/features/processing/useStageRun', () => ({ useStageRun: () => ({}) }));
vi.mock('@/features/processing/PageTimeline', () => ({
  PageTimeline: ({
    step,
    pageId,
    currentId,
  }: {
    step: { stepId: string | null } | null;
    pageId: string | undefined;
    currentId: string | undefined;
  }) => (
    <div
      data-testid="page-history"
      data-step={step === null ? 'none' : (step.stepId ?? 'unsaved')}
      data-page-id={pageId ?? ''}
      data-current-id={currentId ?? ''}
    />
  ),
}));

const SAVED = recipe('r1');
const BAR = barStepsOf(SAVED, [deskew()]);

describe('ProcessingPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  const items = joinRows([page('p1')], [row('p1', { version: version('stage-version') })]);

  function workspaceOf(): StepWorkspace {
    const open = BAR[0];
    if (open === undefined) {
      throw new Error('The recipe of the test has no step.');
    }
    return {
      steps: BAR,
      open,
      states: new Map(),
      page: stepPage(open.stepId, 'found', { version: version('step-version') }),
      counts: countStep([]),
      rows: [],
      neighbours: neighboursOf(BAR, open),
    };
  }

  function render(state: ReturnType<typeof processing>, withStep: boolean): void {
    const workspace = workspaceOf();
    act(() =>
      root.render(
        <ProcessingPanel
          processing={state}
          items={items}
          current={items[0]}
          selected={new Set()}
          editor={null}
          step={
            withStep && workspace.open !== null
              ? { workspace, step: workspace.open, pageLabel: '1', onOpen: () => undefined }
              : undefined
          }
        />,
      ),
    );
  }

  /** What the scrolling area of the panel holds, in order: the sections, then the history. */
  function areaIds(): string[] {
    return Array.from(
      container.querySelector('[data-testid="stage-panel-scroll"]')?.children ?? [],
    ).map((child) => child.getAttribute('data-testid') ?? '');
  }

  /** The names of the sections inside the body of the panel, which the history is not part of. */
  function sectionIds(): string[] {
    const body = container.querySelector('[data-testid="stage-panel-scroll"]')?.firstElementChild;
    return Array.from(body?.children ?? []).map((child) => child.getAttribute('data-testid') ?? '');
  }

  const history = (): HTMLElement | null => container.querySelector('[data-testid="page-history"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('ends the scrolling area with the history of the step open in the bar, after every section', () => {
    render(processing({ recipes: [SAVED], recipe: SAVED }), true);

    expect(sectionIds()).toEqual(['step-panel', 'recipe-section', 'content-type', 'this-page']);
    expect(areaIds()).toEqual(['', 'page-history']);
    expect(history()?.getAttribute('data-step')).toBe(BAR[0]?.stepId);
    expect(history()?.getAttribute('data-page-id')).toBe('p1');
  });

  it('stands the page on the result of the open step, which is the version of the step on the page', () => {
    render(processing({ recipes: [SAVED], recipe: SAVED }), true);

    expect(history()?.getAttribute('data-current-id')).toBe('step-version');
  });

  it('draws the history once on a stage with a bar while no step is open, with no step and the results of the stage', () => {
    render(processing({ recipes: [SAVED], recipe: SAVED }), false);

    expect(container.querySelectorAll('[data-testid="page-history"]')).toHaveLength(1);
    expect(areaIds().at(-1)).toBe('page-history');
    expect(history()?.getAttribute('data-step')).toBe('none');
    expect(history()?.getAttribute('data-current-id')).toBe('stage-version');
  });

  it('ends the panel of a stage without a bar with the history of the step open in the list of the recipe', () => {
    const state = processing({
      stage: 'page-split',
      recipes: [SAVED],
      recipe: SAVED,
      openId: 'step-0',
    });
    render(state, false);

    expect(areaIds().at(-1)).toBe('page-history');
    expect(history()?.getAttribute('data-step')).toBe(state.steps[0]?.stepId);
    expect(history()?.getAttribute('data-current-id')).toBe('stage-version');
  });

  it('draws the history of the stage on a stage without a bar while no step is open in the list', () => {
    render(processing({ stage: 'page-split', recipes: [SAVED], recipe: SAVED }), false);

    expect(history()?.getAttribute('data-step')).toBe('none');
  });

  it('draws the history while the stage loads, and when it fails to load', () => {
    render(processing({ ready: false }), false);
    expect(areaIds().at(-1)).toBe('page-history');

    render(processing({ failed: true }), false);
    expect(areaIds().at(-1)).toBe('page-history');
    expect(container.querySelectorAll('[data-testid="page-history"]')).toHaveLength(1);
  });

  it('hands the history no page when none is open', () => {
    act(() =>
      root.render(
        <ProcessingPanel
          processing={processing({ recipes: [SAVED], recipe: SAVED })}
          items={[]}
          current={undefined}
          selected={new Set()}
          editor={null}
        />,
      ),
    );

    expect(history()?.getAttribute('data-page-id')).toBe('');
  });
});
