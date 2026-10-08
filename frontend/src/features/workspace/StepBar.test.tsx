import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FigureState } from '@/api';
import { binarize, deskew, processor, recipe, step } from '@/features/processing/fixtures';
import { StepBar } from '@/features/workspace/StepBar';
import { barStepsOf, type StepStates } from '@/features/workspace/steps';

/**
 * The bar of the steps under the row above the canvas: the steps in order with their numbers, a
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
      step('geometry.deskew', { step_id: 'b' }),
      step('geometry.deskew', { step_id: 'c', enabled: false }),
    ],
  }),
  CATALOGUE,
);

describe('StepBar', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onOpen = vi.fn();

  function render(
    openId: string | undefined,
    states: Record<string, FigureState | null> = {},
    actions?: React.ReactNode,
    recipePicker?: React.ReactNode,
  ): void {
    act(() =>
      root.render(
        <StepBar
          steps={STEPS}
          openId={openId}
          states={new Map(Object.entries(states)) as StepStates}
          recipePicker={recipePicker}
          onOpen={onOpen}
          actions={actions}
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

  it('opens a step that is pressed and never closes the open one when it is pressed again', () => {
    render('b');
    act(() => buttons()[0]?.click());
    act(() => buttons()[1]?.click());

    expect(onOpen.mock.calls).toEqual([['a'], ['b']]);
  });

  it('draws the choice of the set of steps the caller gives it before the steps', () => {
    render(undefined, {}, undefined, <select data-testid="picker" />);

    const picker = container.querySelector('[data-testid="picker"]');
    expect(picker?.nextElementSibling).toBe(container.querySelector('ol'));
  });

  it('draws the actions the caller gives it after the last step, outside the list of steps', () => {
    render(undefined, {}, <button type="button" data-testid="action" />);

    const action = container.querySelector('[data-testid="action"]');
    const list = container.querySelector('ol');
    expect(action).not.toBeNull();
    expect(list?.contains(action)).toBe(false);
    expect(list?.nextElementSibling).toBe(action);
  });

  it('holds the buttons of the side panels at its two ends, as the one row above the canvas', () => {
    act(() =>
      root.render(
        <StepBar
          steps={STEPS}
          openId={undefined}
          states={new Map() as StepStates}
          onOpen={onOpen}
          leading={<button type="button" data-testid="toggle-strip" />}
          trailing={<button type="button" data-testid="toggle-panel" />}
        />,
      ),
    );

    const bar = container.querySelector('[data-testid="step-bar"]');
    expect(container.children).toHaveLength(1);
    expect(bar?.firstElementChild).toBe(container.querySelector('[data-testid="toggle-strip"]'));
    expect(bar?.lastElementChild).toBe(container.querySelector('[data-testid="toggle-panel"]'));
    expect(bar?.querySelectorAll('ol')).toHaveLength(1);
  });

  describe('for the steps of Cleanup', () => {
    // The recipe of text pages of Cleanup: the three steps that clean the pages of text, then the one the reader fills zones with
    const CLEANUP = barStepsOf(
      recipe('c', {
        stage: 'cleanup',
        steps: [
          step('cleanup.binarize', { step_id: 'bin' }),
          step('cleanup.despeckle', { step_id: 'dust' }),
          step('cleanup.thickness', { step_id: 'thick' }),
          step('cleanup.eraser', { step_id: 'fill' }),
        ],
      }),
      [
        binarize({ title: 'Binarization' }),
        processor('cleanup.despeckle', { title: 'Despeckle', stage: 'cleanup' }),
        processor('cleanup.thickness', { title: 'Thickness', stage: 'cleanup' }),
        processor('cleanup.eraser', { title: 'Fill zones', stage: 'cleanup' }),
      ],
    );

    function renderCleanup(
      openId: string | undefined,
      states: Record<string, FigureState> = {},
    ): void {
      act(() =>
        root.render(
          <StepBar
            steps={CLEANUP}
            openId={openId}
            states={new Map(Object.entries(states)) as StepStates}
            onOpen={onOpen}
          />,
        ),
      );
    }

    it('lists the four steps in the order they run', () => {
      renderCleanup(undefined);

      expect(buttons().map((button) => button.getAttribute('data-step-id'))).toEqual([
        'bin',
        'dust',
        'thick',
        'fill',
      ]);
      expect(buttons().map((button) => button.getAttribute('aria-label'))).toEqual([
        'Open step 1, Binarization',
        'Open step 2, Despeckle',
        'Open step 3, Thickness',
        'Open step 4, Fill zones',
      ]);
    });

    it('opens the step of Thickness that is pressed, and shows the state of each on the open page', () => {
      renderCleanup('thick', { bin: 'found', dust: 'found', thick: 'by-hand', fill: 'default' });
      act(() => buttons()[2]?.click());

      expect(buttons().map((button) => button.getAttribute('data-state'))).toEqual([
        'found',
        'found',
        'by-hand',
        'default',
      ]);
      expect(buttons()[2]?.getAttribute('aria-current')).toBe('step');
      expect(onOpen).toHaveBeenCalledWith('thick');
    });
  });
});
