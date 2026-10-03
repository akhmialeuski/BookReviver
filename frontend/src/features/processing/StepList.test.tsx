import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, recipe, step, whole } from '@/features/processing/fixtures';
import { draftOf } from '@/features/processing/recipe';
import { StepList } from '@/features/processing/StepList';

/**
 * The list of steps of a recipe: each one with its title, a switch, a way to remove it and its settings when it is open.
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
    onOpen: vi.fn(),
    onMove: vi.fn(),
    onToggle: vi.fn(),
    onRemove: vi.fn(),
    onChange: vi.fn(),
    onCondition: vi.fn(),
  };

  function render(
    steps = STEPS,
    openId: string | undefined = undefined,
    pageValuesOf?: (step: (typeof STEPS)[number]) => Record<string, unknown>,
  ): void {
    act(() =>
      root.render(
        <StepList
          steps={steps}
          catalogue={[deskew(), whole()]}
          openId={openId}
          pageValuesOf={pageValuesOf}
          {...handlers}
        />,
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
    expect(handlers.onOpen).toHaveBeenLastCalledWith('step-0');

    render(STEPS, 'step-0');
    act(() => {
      steps()[0]?.querySelector<HTMLElement>('[data-testid="step-toggle"]')?.click();
    });
    expect(handlers.onOpen).toHaveBeenLastCalledWith(undefined);
  });

  it('offers the conditions of a step in its settings, and reports the one chosen', () => {
    render(STEPS, 'step-0');
    const select = steps()[0]?.querySelector<HTMLSelectElement>('[data-testid="step-condition"]');

    expect(select?.value).toBe('all');
    expect([...(select?.options ?? [])].map((option) => option.textContent)).toEqual([
      'All pages',
      'Text pages',
      'Pictures',
      'Colour pictures',
      'Black-and-white pictures',
    ]);
    act(() => {
      if (select !== null && select !== undefined) {
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(
          select,
          'pictures',
        );
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    expect(handlers.onCondition).toHaveBeenCalledWith('step-0', 'pictures');
  });

  it('shows the condition a step was saved with', () => {
    render(
      STEPS.map((entry) => ({ ...entry, appliesTo: 'text' })),
      'step-0',
    );

    expect(
      steps()[0]?.querySelector<HTMLSelectElement>('[data-testid="step-condition"]')?.value,
    ).toBe('text');
  });

  it('draws the settings of the open step only', () => {
    render(STEPS, 'step-0');

    expect(steps()[0]?.querySelector('form')?.textContent).toContain('Largest slant');
    expect(steps()[1]?.querySelector('form')).toBeNull();
  });

  it('marks in the form of the open step the fields the open page changes for itself', () => {
    render(STEPS, 'step-0', () => ({ min_confidence: 0.6 }));

    const labels = [...(steps()[0]?.querySelectorAll('form label') ?? [])].map(
      (label) => label.textContent,
    );
    expect(labels).toEqual(['Largest slant', 'Least confidence · changed for this page']);
  });

  it('draws no mark when no page is open', () => {
    render(STEPS, 'step-0');

    expect(steps()[0]?.textContent).not.toContain('changed for this page');
  });

  it('switches a step off and on with its switch', () => {
    render();

    act(() => {
      steps()[0]?.querySelector<HTMLElement>('[data-testid="step-enabled"]')?.click();
    });

    expect(handlers.onToggle).toHaveBeenCalledWith('step-0');
  });

  it('removes a step', () => {
    render();

    act(() => {
      steps()[1]?.querySelector<HTMLElement>('[data-testid="step-remove"]')?.click();
    });

    expect(handlers.onRemove).toHaveBeenCalledWith('step-1');
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

  describe('with a way to run the recipe up to a step', () => {
    const run = {
      choices: [
        { scope: 'page', count: 1 },
        { scope: 'all', count: 8 },
      ] as const,
      describe: (scope: string, count: number) => `${scope} ${count}`,
      disabled: false,
      passed: (index: number) => 8 - index * 3,
      total: 8,
      onRun: vi.fn(),
    };

    function renderRunnable(steps = STEPS, control = run): void {
      act(() =>
        root.render(
          <StepList
            steps={steps}
            catalogue={[deskew(), whole()]}
            openId={undefined}
            run={control}
            {...handlers}
          />,
        ),
      );
    }

    async function chooseOn(stepIndex: number, scope: string): Promise<void> {
      const trigger = steps()[stepIndex]?.querySelector<HTMLElement>('[data-testid="step-run"]');
      await act(async () => {
        trigger?.focus();
        trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      });
      await act(async () => {
        document.body.querySelector<HTMLElement>(`[data-testid="step-run-${scope}"]`)?.click();
      });
    }

    afterEach(() => {
      document.body.querySelectorAll('[role="menu"]').forEach((node) => {
        node.remove();
      });
    });

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

    it('runs up to the step over the scope chosen, counting the steps from zero', async () => {
      run.onRun.mockReset();
      renderRunnable();

      await chooseOn(1, 'all');

      expect(run.onRun).toHaveBeenCalledWith(1, 'all');
    });

    it('keeps the run off for a step when every step up to it is off', () => {
      renderRunnable(STEPS.map((entry, index) => ({ ...entry, enabled: index === 1 })));

      const triggers = steps().map((item) =>
        item.querySelector<HTMLButtonElement>('[data-testid="step-run"]'),
      );
      expect(triggers.map((trigger) => trigger?.disabled)).toEqual([true, false]);
    });

    it('keeps the run off while the whole run is', () => {
      renderRunnable(STEPS, { ...run, disabled: true });

      expect(
        steps().every(
          (item) => item.querySelector<HTMLButtonElement>('[data-testid="step-run"]')?.disabled,
        ),
      ).toBe(true);
    });

    it('offers nothing for a recipe of a single step, which runs through it anyway', () => {
      renderRunnable(STEPS.slice(0, 1));

      expect(container.querySelector('[data-testid="step-run"]')).toBeNull();
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
});
