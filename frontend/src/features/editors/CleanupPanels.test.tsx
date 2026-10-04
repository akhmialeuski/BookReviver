import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrushPanel } from '@/features/editors/BrushPanel';
import { RegionsPanel } from '@/features/editors/RegionsPanel';
import { newZone } from '@/features/editors/regions';
import { ZoneMode } from '@/features/editors/shapes';

/** The parts of the picture zone editor and of the brush editor in the panel. */

const SIZE = { width: 1000, height: 1500 };

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

describe('the panels of the cleanup editors', () => {
  let container: HTMLDivElement;
  let root: Root;

  const byTestId = (id: string): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`);

  function press(button: HTMLButtonElement | null): void {
    act(() => button?.click());
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  describe('RegionsPanel', () => {
    function render(props: {
      zones?: ReturnType<typeof newZone>[];
      size?: typeof SIZE | null;
      disabled?: boolean;
    }) {
      const onCommit = vi.fn();
      act(() =>
        root.render(
          <RegionsPanel
            shape={{ zones: props.zones ?? [] }}
            processorKey="cleanup.binarize"
            params={{}}
            disabled={props.disabled ?? false}
            size={props.size === undefined ? SIZE : props.size}
            onChange={vi.fn()}
            onCommit={onCommit}
          />,
        ),
      );
      return onCommit;
    }

    it('says how a zone is shaped, and says the reader has drawn none', () => {
      render({});

      expect(byTestId('regions-hint')?.textContent).toContain('drag the corners');
      expect(container.textContent).toContain('You have drawn no zones');
      expect(byTestId('regions-list')).toBeNull();
    });

    it('adds a zone that adds a picture, and a zone that removes one, in the middle of the page', () => {
      const onCommit = render({});

      press(byTestId('regions-add'));
      press(byTestId('regions-remove'));

      expect(onCommit.mock.calls[0]?.[0]).toEqual({ zones: [newZone(ZoneMode.Add, SIZE)] });
      expect(onCommit.mock.calls[1]?.[0]).toEqual({ zones: [newZone(ZoneMode.Remove, SIZE)] });
    });

    it('lists the zones by their kind and their number, and deletes the one that was chosen', () => {
      const zones = [newZone(ZoneMode.Add, SIZE), newZone(ZoneMode.Remove, SIZE)];
      const onCommit = render({ zones });

      expect(
        [...container.querySelectorAll('[data-testid="regions-list"] li span')].map(
          (name) => name.textContent,
        ),
      ).toEqual(['Picture 1', 'Not a picture 2']);
      press(
        container.querySelectorAll<HTMLButtonElement>('[data-testid="regions-delete"]')[0] ?? null,
      );

      expect(onCommit).toHaveBeenCalledWith({ zones: [zones[1]] });
    });

    it('offers no new zone while the size of the page is not known or a save is going', () => {
      render({ size: null });
      expect(byTestId('regions-add')?.disabled).toBe(true);

      render({ disabled: true });
      expect(byTestId('regions-remove')?.disabled).toBe(true);
    });
  });

  describe('BrushPanel', () => {
    function render(strokes: number, disabled = false) {
      const onCommit = vi.fn();
      act(() =>
        root.render(
          <BrushPanel
            processorKey="cleanup.eraser"
            params={{}}
            shape={{
              strokes: Array.from({ length: strokes }, () => ({
                radius: 5,
                points: [{ x: 1, y: 1 }],
              })),
            }}
            disabled={disabled}
            size={SIZE}
            onChange={vi.fn()}
            onCommit={onCommit}
          />,
        ),
      );
      return onCommit;
    }

    it('says how the brush works and how large it is', () => {
      render(0);

      expect(byTestId('brush-hint')?.textContent).toContain('Brush over');
      expect(byTestId('brush-size')?.textContent).toBe('2 % of the page width');
    });

    it('takes every stroke back with one button, which waits for a stroke', () => {
      const none = render(0);
      expect(byTestId('brush-clear')?.disabled).toBe(true);
      expect(none).not.toHaveBeenCalled();

      const onCommit = render(3);
      press(byTestId('brush-clear'));

      expect(onCommit).toHaveBeenCalledWith({ strokes: [] });
    });
  });
});
