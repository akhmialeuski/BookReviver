import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LiveDropRects } from '@/features/order/LiveDropRects';

/**
 * The re-measuring of the drop targets of the Order grid: asked of dnd-kit whenever the rows move while a page is held,
 * and not otherwise.
 *
 * The context of dnd-kit is replaced by the two things the component reads, so the test sees every request for a
 * measurement as dnd-kit would get it.
 */

const context = vi.hoisted(() => ({
  active: null as object | null,
  measureDroppableContainers: vi.fn(),
}));

vi.mock('@dnd-kit/core', () => ({
  useDndContext: () => context,
}));

describe('LiveDropRects', () => {
  let container: HTMLDivElement;
  let root: Root;

  function render(layout: string): void {
    // The grid keys the component by the places of its rows, so a change of them mounts it again
    act(() => root.render(<LiveDropRects key={layout} />));
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    context.active = null;
    context.measureDroppableContainers.mockReset();
    container = document.createElement('div');
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  });

  it('measures every drop target again when the rows move while a page is held', () => {
    context.active = {};
    render('0,309');
    context.measureDroppableContainers.mockClear();

    render('0,295');

    expect(context.measureDroppableContainers).toHaveBeenCalledExactlyOnceWith([]);
  });

  it('measures nothing while no page is held, whatever the rows do', () => {
    render('0,309');
    render('0,295');

    expect(context.measureDroppableContainers).not.toHaveBeenCalled();
  });

  it('measures nothing again for rows that stayed where they were', () => {
    context.active = {};
    render('0,309');
    context.measureDroppableContainers.mockClear();

    render('0,309');

    expect(context.measureDroppableContainers).not.toHaveBeenCalled();
  });
});
