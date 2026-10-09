import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { type PanelToggles, StageWorkspace } from '@/features/workspace/StageWorkspace';
import { useAfterPick } from '@/features/workspace/stripSheet';
import { NARROW_QUERY } from '@/shared/hooks/useMediaQuery';

/**
 * The workspace in a narrow window: the canvas alone, the strip and the panel as sheets that the two buttons open, and a
 * strip that closes its sheet when a page is picked. In a wide window the three parts stand side by side.
 */

function Strip(): React.JSX.Element {
  const afterPick = useAfterPick();
  return (
    <button type="button" data-testid="pick" onClick={afterPick}>
      Pick a page
    </button>
  );
}

describe('StageWorkspace', () => {
  let container: HTMLDivElement;
  let root: Root;

  const find = (id: string): Element | null => document.body.querySelector(`[data-testid="${id}"]`);
  const click = (id: string): void => {
    act(() => find(id)?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  };

  function render(stepBar?: (toggles: PanelToggles) => React.ReactNode): void {
    act(() =>
      root.render(
        <StageWorkspace
          strip={<Strip />}
          canvasHeader={<span data-testid="canvas-header">Header</span>}
          stepBar={stepBar}
          canvas={<div data-testid="canvas">Canvas</div>}
          panel={<p data-testid="panel-body">Panel</p>}
        />,
      ),
    );
  }

  /** A bar of steps that stands where the workspace puts it and holds the two buttons it is given. */
  const bar = (toggles: PanelToggles): React.JSX.Element => (
    <nav data-testid="step-bar">
      {toggles.strip}
      <span data-testid="steps">Steps</span>
      {toggles.panel}
    </nav>
  );

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

  describe('in a narrow window', () => {
    beforeEach(() => {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: query === NARROW_QUERY,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }));
    });

    it('draws the canvas alone, with the strip and the panel out of sight', () => {
      render();

      expect(find('canvas')).not.toBeNull();
      expect(find('pick')).toBeNull();
      expect(find('panel-body')).toBeNull();
      expect(container.querySelector('[data-separator]')).toBeNull();
    });

    it('puts the two buttons at the ends of the bar of the steps and draws no row above it', () => {
      render(bar);

      const steps = find('step-bar');
      expect(steps?.firstElementChild).toBe(find('toggle-strip'));
      expect(steps?.lastElementChild).toBe(find('toggle-panel'));
      expect(steps?.previousElementSibling).toBeNull();
      expect(steps?.parentElement?.firstElementChild).toBe(steps);
      expect(find('canvas-header')).toBeNull();
    });

    it('gives a stage without a bar of steps a slim row with the two buttons and its own header between them', () => {
      render();

      const row = find('toggle-strip')?.parentElement;
      expect(row?.contains(find('canvas-header'))).toBe(true);
      expect(row?.contains(find('toggle-panel'))).toBe(true);
      expect(find('step-bar')).toBeNull();
    });

    it('opens the strip and the panel from the two buttons and closes them again', () => {
      render();

      click('toggle-strip');
      expect(find('strip-sheet')).not.toBeNull();
      expect(find('pick')).not.toBeNull();

      click('pick');
      expect(find('strip-sheet')).toBeNull();

      click('toggle-panel');
      expect(find('panel-body')).not.toBeNull();
    });
  });

  it('puts the three parts side by side in a wide window', () => {
    vi.stubGlobal('matchMedia', undefined);
    // The resizable panels watch their group for size changes, which jsdom cannot do
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe = () => undefined;
        unobserve = () => undefined;
        disconnect = () => undefined;
      },
    );
    render();

    expect(find('canvas')).not.toBeNull();
    expect(find('pick')).not.toBeNull();
    expect(find('panel-body')).not.toBeNull();
    expect(find('strip-sheet')).toBeNull();
  });
});
