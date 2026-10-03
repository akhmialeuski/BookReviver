import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FigureState } from '@/api';
import { deskew, processor, recipe, step } from '@/features/processing/fixtures';
import { StepBar } from '@/features/workspace/StepBar';
import { barStepsOf, type StepStates } from '@/features/workspace/steps';

/**
 * The bar of the steps under the row above the canvas: the steps in order with their numbers, a mark for a condition, a
 * dot with the state of the shape on the open page, an underline for the open step, and a press that opens or closes one.
 */

const CATALOGUE = [
  processor('geometry.perspective', { title: 'Perspective' }),
  deskew({ title: 'Deskew' }),
];
const STEPS = barStepsOf(
  recipe('r', {
    steps: [
      step('geometry.perspective', { step_id: 'a' }),
      step('geometry.deskew', { step_id: 'b', applies_to: 'text' }),
      step('geometry.deskew', { step_id: 'c', applies_to: 'pictures', enabled: false }),
    ],
  }),
  CATALOGUE,
);

describe('StepBar', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onOpen = vi.fn();
  const onChooseRecipe = vi.fn();

  function render(
    openId: string | undefined,
    states: Record<string, FigureState | null> = {},
    recipes = [{ id: 'r', name: 'Book' }],
  ): void {
    act(() =>
      root.render(
        <StepBar
          steps={STEPS}
          openId={openId}
          states={new Map(Object.entries(states)) as StepStates}
          recipes={recipes}
          recipeId="r"
          onChooseRecipe={onChooseRecipe}
          onOpen={onOpen}
        />,
      ),
    );
  }

  const buttons = (): HTMLButtonElement[] => [
    ...container.querySelectorAll<HTMLButtonElement>('[data-testid="bar-step"]'),
  ];

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    onOpen.mockReset();
    onChooseRecipe.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('lists the steps in order by number and title', () => {
    render(undefined);

    expect(buttons().map((button) => button.getAttribute('data-step-id'))).toEqual(['a', 'b', 'c']);
    expect(buttons().map((button) => button.getAttribute('title'))).toEqual([
      '1 · Perspective · Reading the page',
      '2 · Deskew · Reading the page',
      '3 · Deskew · Reading the page',
    ]);
  });

  it('marks a step that processes some pages only, with the words of its condition for a tooltip', () => {
    render(undefined);
    const marks = [...container.querySelectorAll('[data-testid="bar-step-mark"]')];

    expect(
      marks.map((mark) => [
        mark.getAttribute('data-mark'),
        mark.textContent,
        mark.getAttribute('title'),
      ]),
    ).toEqual([
      ['text', '¶', 'Text pages'],
      ['picture', '▣', 'Pictures'],
    ]);
  });

  it('shows the state of each step on the open page by a dot and by a word', () => {
    render(undefined, { a: 'default', b: 'by-hand', c: 'skipped' });

    expect(buttons().map((button) => button.getAttribute('data-state'))).toEqual([
      'default',
      'by-hand',
      'skipped',
    ]);
    expect(buttons()[1]?.textContent).toContain('Set by hand');
    expect(buttons()[2]?.textContent).toContain('Skipped on this page');
  });

  it('says that a step that is off is off', () => {
    render(undefined, { c: 'default' });

    expect(buttons()[2]?.textContent).toContain('Off');
    expect(buttons()[1]?.textContent).not.toContain('Off');
  });

  it('underlines the open step alone', () => {
    render('b');

    expect(buttons().map((button) => button.getAttribute('aria-current'))).toEqual([
      null,
      'step',
      null,
    ]);
  });

  it('opens a step that is pressed and closes the step that is open when it is pressed again', () => {
    render('b');
    act(() => buttons()[0]?.click());
    act(() => buttons()[1]?.click());

    expect(onOpen.mock.calls).toEqual([['a'], [undefined]]);
  });

  it('offers the sets of steps of the stage only when there are several', () => {
    render(undefined);
    expect(container.querySelector('[data-testid="step-bar-recipe"]')).toBeNull();

    render(undefined, {}, [
      { id: 'r', name: 'Book' },
      { id: 'q', name: 'Plates' },
    ]);
    const select = container.querySelector<HTMLSelectElement>('[data-testid="step-bar-recipe"]');
    act(() => {
      if (select !== null) {
        select.value = 'q';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });

    expect(onChooseRecipe).toHaveBeenCalledWith('q');
  });
});
