import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SceneMapping } from '@/features/editors/mapping';
import type { SceneFrame } from '@/features/editors/scene';
import { type ShapeEditing, useShapeEditing } from '@/features/editors/shapeEditing';

/**
 * The saving of a shape the reader moves with the keys or a handle: a run of key presses is saved once after a pause,
 * a handle let go saves at once and replaces a save that was waiting, and a save that was waiting is made for the page
 * it was asked on even when the reader has turned to another one.
 */

const SAVE_DELAY_MS = 600;
const SIZE = { width: 100, height: 100 };

/** The shape of the test: one number, which an arrow key moves by one. */
interface Bar {
  x: number;
}

const START: Bar = { x: 0 };

const FRAME: SceneFrame = {
  mapping: new SceneMapping(
    {
      imageToViewerElementCoordinates: (pixel) => pixel,
      viewerElementToImageCoordinates: (pixel) => pixel,
    },
    SIZE,
    SIZE,
  ),
  stage: SIZE,
  size: SIZE,
  viewRotation: 0,
};

describe('useShapeEditing', () => {
  let container: HTMLDivElement;
  let root: Root;
  let editing: ShapeEditing<Bar>;

  function Probe({ onCommit }: { onCommit: (shape: Bar) => void }): React.JSX.Element {
    editing = useShapeEditing(FRAME, START, vi.fn(), onCommit);
    // A stand-in for the layer that takes the keys, the slider `EditorLayer` draws
    return (
      <div
        role="slider"
        aria-label="Bar"
        aria-valuenow={0}
        tabIndex={0}
        data-testid="layer"
        onKeyDown={editing.onKeyDown((current, step) => ({ x: current.x + step.x }))}
      />
    );
  }

  function render(onCommit: (shape: Bar) => void): void {
    act(() => root.render(<Probe onCommit={onCommit} />));
  }

  function pressRight(): void {
    act(() => {
      container
        .querySelector('[data-testid="layer"]')
        ?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('saves the shape a run of key presses made once, when the keys pause', () => {
    const commit = vi.fn();
    render(commit);

    pressRight();
    pressRight();
    vi.advanceTimersByTime(SAVE_DELAY_MS);

    expect(commit).toHaveBeenCalledExactlyOnceWith({ x: 2 });
  });

  it('saves a handle let go at once, and not again for a key press that came before it', () => {
    const commit = vi.fn();
    render(commit);

    pressRight();
    act(() => editing.change({ x: 50 }));
    editing.release();
    vi.advanceTimersByTime(SAVE_DELAY_MS * 2);

    expect(commit).toHaveBeenCalledExactlyOnceWith({ x: 50 });
  });

  it('saves a waiting shape for the page it was asked on when the reader has turned to another page', () => {
    const forFirst = vi.fn();
    const forSecond = vi.fn();
    render(forFirst);
    pressRight();

    render(forSecond);
    vi.advanceTimersByTime(SAVE_DELAY_MS);

    expect(forFirst).toHaveBeenCalledExactlyOnceWith({ x: 1 });
    expect(forSecond).not.toHaveBeenCalled();
  });
});
