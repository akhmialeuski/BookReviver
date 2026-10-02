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
  };

  function render(steps = STEPS, openId: string | undefined = undefined): void {
    act(() =>
      root.render(
        <StepList steps={steps} catalogue={[deskew(), whole()]} openId={openId} {...handlers} />,
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

  it('draws the settings of the open step only', () => {
    render(STEPS, 'step-0');

    expect(steps()[0]?.querySelector('form')?.textContent).toContain('Largest slant');
    expect(steps()[1]?.querySelector('form')).toBeNull();
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

  it('marks a step whose values are outside their limits', () => {
    render(
      STEPS.map((entry, index) => (index === 0 ? { ...entry, params: { max_angle: 99 } } : entry)),
    );

    expect(steps()[0]?.className).toContain('border-destructive');
    expect(steps()[1]?.className).not.toContain('border-destructive');
  });
});
