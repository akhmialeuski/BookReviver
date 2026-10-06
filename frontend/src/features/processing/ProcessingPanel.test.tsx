import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processing, recipe } from '@/features/processing/fixtures';
import { ProcessingPanel } from '@/features/processing/ProcessingPanel';
import { page, row } from '@/features/workspace/fixtures';
import { barStepsOf, countStep, neighboursOf } from '@/features/workspace/steps';
import { joinRows } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

/**
 * Where the panel of a stage puts the history of the page: at its very end, after every other section, for the step open
 * in the bar or, on a stage without a bar, for the step open in the list of the recipe.
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
vi.mock('@/features/processing/PageHistorySection', () => ({
  PageHistorySection: ({ stepId, pageId }: { stepId: string | null; pageId: string }) => (
    <div data-testid="page-history" data-step-id={stepId ?? ''} data-page-id={pageId} />
  ),
}));

const SAVED = recipe('r1');
const BAR = barStepsOf(SAVED, [deskew()]);

describe('ProcessingPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  const items = joinRows([page('p1')], [row('p1')]);

  function workspaceOf(): StepWorkspace {
    const open = BAR[0];
    if (open === undefined) {
      throw new Error('The recipe of the test has no step.');
    }
    return {
      steps: BAR,
      open,
      states: new Map(),
      page: null,
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

  function sectionIds(): string[] {
    const history = container.querySelector('[data-testid="page-history"]');
    return Array.from(history?.parentElement?.children ?? []).map(
      (child) => child.getAttribute('data-testid') ?? '',
    );
  }

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

  it('ends the panel with the history of the step open in the bar, after every other section', () => {
    render(processing({ recipes: [SAVED], recipe: SAVED }), true);

    expect(sectionIds()).toEqual([
      'step-panel',
      'recipe-section',
      'content-type',
      'this-page',
      'page-history',
    ]);
    const history = container.querySelector('[data-testid="page-history"]');
    expect(history?.getAttribute('data-step-id')).toBe(BAR[0]?.stepId);
    expect(history?.getAttribute('data-page-id')).toBe('p1');
  });

  it('shows no history on a stage with a bar while no step is open', () => {
    render(processing({ recipes: [SAVED], recipe: SAVED }), false);

    expect(container.querySelector('[data-testid="page-history"]')).toBeNull();
  });

  it('ends the panel of a stage without a bar with the history of the step open in the list of the recipe', () => {
    const state = processing({
      stage: 'page-split',
      recipes: [SAVED],
      recipe: SAVED,
      openId: 'step-0',
    });
    render(state, false);

    expect(sectionIds().at(-1)).toBe('page-history');
    expect(
      container.querySelector('[data-testid="page-history"]')?.getAttribute('data-step-id'),
    ).toBe(state.steps[0]?.stepId);
  });
});
