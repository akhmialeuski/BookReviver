import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CanvasToolbar } from '@/features/workspace/CanvasToolbar';

/**
 * The bar at the foot of the canvas: the "Grid" button of a stage that has a grid, and its absence on one that has none,
 * and the "Auto" button that takes the shape set by hand away.
 */

describe('CanvasToolbar', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onToggle = vi.fn();

  function render(
    grid?: { on: boolean; onToggle: () => void },
    auto?: { busy: boolean; onClick: () => void },
  ): void {
    act(() =>
      root.render(
        <CanvasToolbar
          caption="p. 1 · 1 of 3"
          spread={false}
          hasPrevious={false}
          hasNext
          onPrevious={vi.fn()}
          onNext={vi.fn()}
          onToggleSpread={vi.fn()}
          onFit={vi.fn()}
          onZoomIn={vi.fn()}
          onZoomOut={vi.fn()}
          grid={grid}
          auto={auto}
        />,
      ),
    );
  }

  const button = (): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>('[data-testid="canvas-grid-toggle"]');

  const autoButton = (): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>('[data-testid="canvas-auto"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    onToggle.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('draws no Grid button for a stage that has no grid', () => {
    render();

    expect(button()).toBeNull();
  });

  it('shows the Grid button pressed while the grid is on, and switches it when pressed', () => {
    render({ on: true, onToggle });

    expect(button()?.textContent).toContain('Grid');
    expect(button()?.getAttribute('aria-pressed')).toBe('true');
    act(() => button()?.click());
    expect(onToggle).toHaveBeenCalledOnce();

    render({ on: false, onToggle });
    expect(button()?.getAttribute('aria-pressed')).toBe('false');
  });

  it('draws no Auto button while the open step has no shape set by hand', () => {
    render();

    expect(autoButton()).toBeNull();
  });

  it('runs Auto when it is pressed, and disables it while a change is being made', () => {
    const onClick = vi.fn();
    render(undefined, { busy: false, onClick });

    expect(autoButton()?.textContent).toContain('Auto');
    expect(autoButton()?.disabled).toBe(false);
    act(() => autoButton()?.click());
    expect(onClick).toHaveBeenCalledOnce();

    render(undefined, { busy: true, onClick });
    expect(autoButton()?.disabled).toBe(true);
  });
});
