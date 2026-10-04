import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CanvasToolbar } from '@/features/workspace/CanvasToolbar';

/** The bar at the foot of the canvas: the "Grid" button of a stage that has a grid, and its absence on one that has none. */

describe('CanvasToolbar', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onToggle = vi.fn();

  function render(grid?: { on: boolean; onToggle: () => void }): void {
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
        />,
      ),
    );
  }

  const button = (): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>('[data-testid="canvas-grid-toggle"]');

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
});
