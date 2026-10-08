import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  deskew,
  pageValues,
  processing,
  processor,
  recipe,
  step,
  stepSettings,
  whole,
} from '@/features/processing/fixtures';
import type { OrderIssue } from '@/features/processing/order';
import type { PageValues } from '@/features/processing/pageSettings';
import { draftOf } from '@/features/processing/recipe';
import { StepList } from '@/features/processing/StepList';
import { OrderNotice } from '@/features/processing/StepSorter';

/**
 * The list of steps of a recipe, which the panel of a stage without a step bar and the window of the gear both draw: each
 * step with its title, a switch, a way to remove it and, when it is open, its settings.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in. Dragging a step
 * needs the geometry of a layout, which jsdom has none of, so the order is tested on `moveStep` and in a browser.
 */

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const STEPS = draftOf(
  recipe('r', {
    steps: [
      step('geometry.deskew', { params: { max_angle: 5, min_confidence: 0.3 } }),
      step('x.gone'),
    ],
  }),
);

describe('StepList', () => {
  let container: HTMLDivElement;
  let root: Root;
  const handlers = {
    open: vi.fn(),
    move: vi.fn(),
    toggle: vi.fn(),
    remove: vi.fn(),
    change: vi.fn(),
    restoreOrder: vi.fn(),
    setOrderMode: vi.fn(),
  };

  function render(
    steps = STEPS,
    openId: string | undefined = undefined,
    values?: PageValues,
    showParams = true,
    overrides: Parameters<typeof processing>[0] = {},
  ): void {
    act(() =>
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <StepList
            processing={processing({
              steps,
              catalogue: [deskew(), whole()],
              openId,
              ...handlers,
              ...overrides,
            })}
            showParams={showParams}
            values={values}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const steps = (): HTMLElement[] => [
    ...container.querySelectorAll<HTMLElement>('[data-testid="recipe-step"]'),
  ];

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    for (const handler of Object.values(handlers)) {
      handler.mockReset();
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

  it('numbers the steps and gives each the title of its processor', () => {
    render();

    expect(
      steps().map((item) => item.querySelector('[data-testid="step-toggle"]')?.textContent),
    ).toEqual(['1 · Deskew', '2 · x.gone']);
  });

  it('says so for a recipe with no steps', () => {
    render([]);

    expect(container.textContent).toContain('This recipe has no steps');
    expect(container.querySelector('ol')).toBeNull();
  });

  it('opens a step by its title and closes the step that is open', () => {
    render();
    act(() => {
      steps()[0]?.querySelector<HTMLElement>('[data-testid="step-toggle"]')?.click();
    });
    expect(handlers.open).toHaveBeenLastCalledWith('step-0');

    render(STEPS, 'step-0');
    act(() => {
      steps()[0]?.querySelector<HTMLElement>('[data-testid="step-toggle"]')?.click();
    });
    expect(handlers.open).toHaveBeenLastCalledWith(undefined);
  });

  it('draws the settings of the open step only', () => {
    render(STEPS, 'step-0');

    expect(steps()[0]?.querySelector('form')?.textContent).toContain('Largest slant');
    expect(steps()[1]?.querySelector('form')).toBeNull();
  });

  it('draws under each setting of the open step the values the open page and its parts have', () => {
    render(
      STEPS,
      'step-0',
      pageValues({
        settings: [
          stepSettings('id-geometry.deskew', {
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
      }),
    );

    const fields = [...(steps()[0]?.querySelectorAll('[data-testid="field-values"]') ?? [])].map(
      (field) => [field.getAttribute('data-field'), field.textContent],
    );
    expect(fields).toEqual([
      ['max_angle', expect.stringContaining('Even pages 3')],
      ['min_confidence', expect.stringContaining('p. 143 0.6')],
    ]);
  });

  it('draws no values when no page is open', () => {
    render(STEPS, 'step-0');

    expect(steps()[0]?.querySelector('[data-testid="field-values"]')).toBeNull();
  });

  it('switches a step off and on with its switch', () => {
    render();

    act(() => {
      steps()[0]?.querySelector<HTMLElement>('[data-testid="step-enabled"]')?.click();
    });

    expect(handlers.toggle).toHaveBeenCalledWith('step-0');
  });

  it('removes a step', () => {
    render();

    act(() => {
      steps()[1]?.querySelector<HTMLElement>('[data-testid="step-remove"]')?.click();
    });

    expect(handlers.remove).toHaveBeenCalledWith('step-1');
  });

  it('says a step is off, and that it is kept', () => {
    render(
      STEPS.map((entry) => ({ ...entry, enabled: false })),
      'step-0',
    );

    expect(steps()[0]?.textContent).toContain('Off: the step is kept');
  });

  it('keeps a step whose processor is not installed, says so, and still lets it be removed', () => {
    render(STEPS, 'step-1');

    expect(steps()[1]?.textContent).toContain('This step is not installed on this machine.');
    expect(steps()[1]?.querySelector('[data-testid="step-remove"]')).not.toBeNull();
  });

  it('gives each step a handle to drag it by, named for the step', () => {
    render();

    const names = steps().map((item) => item.querySelector('button')?.getAttribute('aria-label'));
    expect(names).toEqual(['Move the Deskew step', 'Move the x.gone step']);
  });

  describe('with the progress of the pages', () => {
    const progress = {
      passed: (index: number) => 8 - index * 3,
      total: 8,
    };

    function renderRunnable(steps = STEPS): void {
      act(() =>
        root.render(
          <StepList
            processing={processing({
              steps,
              catalogue: [deskew(), whole()],
              ...handlers,
            })}
            showParams
            progress={progress}
          />,
        ),
      );
    }

    it('says how many pages passed each step that is on', () => {
      renderRunnable();

      expect(
        steps().map((item) => item.querySelector('[data-testid="step-passed"]')?.textContent),
      ).toEqual(['8 of 8 pages passed', '5 of 8 pages passed']);
    });

    it('says nothing of the pages for a step that is off', () => {
      renderRunnable(STEPS.map((entry, index) => ({ ...entry, enabled: index === 0 })));

      expect(steps()[1]?.querySelector('[data-testid="step-passed"]')).toBeNull();
    });

    it('says nothing of the pages for a recipe of a single step', () => {
      renderRunnable(STEPS.slice(0, 1));

      expect(container.querySelector('[data-testid="step-passed"]')).toBeNull();
    });
  });

  it('marks a step whose values are outside their limits', () => {
    render(
      STEPS.map((entry, index) => (index === 0 ? { ...entry, params: { max_angle: 99 } } : entry)),
    );

    expect(steps()[0]?.className).toContain('border-destructive');
    expect(steps()[1]?.className).not.toContain('border-destructive');
  });
  describe('with the order guarded', () => {
    const REASON =
      'Deskew reads the slant of the lines on an upright sheet, so it usually comes after Perspective.';
    const REQUIRED_REASON = 'Margins cannot come before Select content.';

    function issue(kind: 'usual' | 'required', reason: string): OrderIssue {
      return { stepId: 'step-0', otherId: 'step-1', kind, reason };
    }

    function guarded(issues: OrderIssue[], openId: string | undefined = undefined): void {
      render(STEPS, openId, undefined, true, {
        catalogue: [deskew(), processor('x.gone')],
        orderIssues: new Map(issues.length === 0 ? [] : [['step-0', issues]]),
      });
    }

    const mark = (index: number): HTMLElement | null | undefined =>
      steps()[index]?.querySelector<HTMLElement>('[data-testid="step-order-mark"]');

    it('marks a step that is off its usual place, with the reason on the line below it', () => {
      guarded([issue('usual', REASON)]);

      expect(mark(0)?.textContent).toBe('Out of place');
      expect(mark(0)?.dataset.kind).toBe('usual');
      expect(mark(0)?.title).toBe(REASON);
      expect(steps()[0]?.querySelector('[data-testid="step-order-reason"]')?.textContent).toBe(
        REASON,
      );
      expect(mark(1)).toBeNull();
      expect(steps()[1]?.querySelector('[data-testid="step-order-reason"]')).toBeNull();
    });

    it('marks a step that stands where it cannot work as such', () => {
      guarded([issue('required', REQUIRED_REASON)]);

      expect(mark(0)?.textContent).toBe('Cannot work here');
      expect(mark(0)?.dataset.kind).toBe('required');
    });

    it('draws no mark for steps in their place, and none without a guard', () => {
      guarded([]);
      expect(container.querySelector('[data-testid="step-order-mark"]')).toBeNull();

      render();
      expect(container.querySelector('[data-testid="step-order-mark"]')).toBeNull();
    });

    it('gives every reason in the settings of the step', () => {
      guarded([issue('usual', REASON), issue('required', REQUIRED_REASON)], 'step-0');

      const details = steps()[0]?.querySelector('[data-testid="step-order-details"]');
      expect(details?.textContent).toContain(REASON);
      expect(details?.textContent).toContain(REQUIRED_REASON);
      expect(steps()[0]?.querySelector('[data-testid="step-order-reason"]')).toBeNull();
    });

    it('offers one button that restores the usual order while a step is off its place', () => {
      guarded([issue('usual', REASON)]);

      const restore = container.querySelector<HTMLButtonElement>(
        '[data-testid="steps-restore-order"]',
      );
      expect(restore?.textContent).toBe('Restore the usual order');
      act(() => restore?.click());
      expect(handlers.restoreOrder).toHaveBeenCalledTimes(1);
    });

    it('offers no button to restore the order while every step is in its place', () => {
      guarded([]);

      expect(container.querySelector('[data-testid="steps-restore-order"]')).toBeNull();
    });
  });

  describe('the order of the steps', () => {
    it('switches the order the draft is saved in', () => {
      render();

      act(() => container.querySelector<HTMLElement>('[data-testid="order-free"]')?.click());

      expect(handlers.setOrderMode).toHaveBeenCalledWith('free');
    });
  });

  describe('where the settings of a step are set elsewhere', () => {
    it('draws the summary of the processor in place of the form of its settings', () => {
      render(STEPS, 'step-0', undefined, false, {
        catalogue: [deskew({ summary: 'Straightens the lines' }), whole()],
      });

      expect(steps()[0]?.textContent).toContain('Straightens the lines');
      expect(steps()[0]?.querySelector('input[type="range"], [role="slider"]')).toBeNull();
    });
  });

  describe('OrderNotice', () => {
    it('refuses the place in the usual order, with the reason, as an alert', () => {
      act(() =>
        root.render(<OrderNotice mode="usual" issue={{ reason: 'Not before Select content.' }} />),
      );

      const notice = container.querySelector('[data-testid="order-refusal"]');
      expect(notice?.getAttribute('role')).toBe('alert');
      expect(notice?.textContent).toBe('This place is not allowed. Not before Select content.');
      expect(notice?.className).toContain('text-destructive');
    });

    it('allows the place in the free order, and still gives the reason', () => {
      act(() =>
        root.render(<OrderNotice mode="free" issue={{ reason: 'Not before Select content.' }} />),
      );

      const notice = container.querySelector('[data-testid="order-refusal"]');
      expect(notice?.textContent).toBe('Allowed in the free order. Not before Select content.');
      expect(notice?.className).toContain('text-status-attention');
    });
  });
});
