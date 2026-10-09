import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SceneMapping } from '@/features/editors/mapping';
import type { SceneFrame } from '@/features/editors/scene';
import { type ShapeEditing, useShapeEditing } from '@/features/editors/shapeEditing';

/**
 * What the editing of a shape asks for while the reader moves it with the keys or a handle: a key press asks for a save
 * that waits for a pause, with the shape it made, and a handle let go asks for a save at once of the latest shape. When
 * the two are made, and which one wins, is decided by the editor session and tested with it. A shape still in motion when
 * the editor goes away is saved at once, once.
 */

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

  function Probe({
    onCommit,
    onCommitLater,
  }: {
    onCommit: (shape: Bar) => void;
    onCommitLater: (shape: Bar) => void;
  }): React.JSX.Element {
    editing = useShapeEditing(FRAME, START, vi.fn(), onCommit, onCommitLater);
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

  function render(onCommit: (shape: Bar) => void, onCommitLater: (shape: Bar) => void): void {
    act(() => root.render(<Probe onCommit={onCommit} onCommitLater={onCommitLater} />));
  }

  function pressRight(): void {
    act(() => {
      container
        .querySelector('[data-testid="layer"]')
        ?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  });

  it('asks for a save that waits for a pause with the shape each key press made, which builds on the one before', () => {
    const commit = vi.fn();
    const commitLater = vi.fn();
    render(commit, commitLater);

    pressRight();
    pressRight();

    expect(commitLater.mock.calls).toEqual([[{ x: 1 }], [{ x: 2 }]]);
    expect(commit).not.toHaveBeenCalled();
  });

  it('saves the latest shape at once for a handle let go', () => {
    const commit = vi.fn();
    const commitLater = vi.fn();
    render(commit, commitLater);

    act(() => editing.change({ x: 50 }));
    editing.release();

    expect(commit).toHaveBeenCalledExactlyOnceWith({ x: 50 });
    expect(commitLater).not.toHaveBeenCalled();
  });

  it('saves nothing for a release when no shape is in motion', () => {
    const commit = vi.fn();
    render(commit, vi.fn());

    editing.release();

    expect(commit).not.toHaveBeenCalled();
  });

  it('saves a shape still in motion when the editor goes away, with the latest one', () => {
    const commit = vi.fn();
    render(commit, vi.fn());

    act(() => editing.change({ x: 20 }));
    act(() => editing.change({ x: 50 }));
    act(() => root.unmount());

    expect(commit).toHaveBeenCalledExactlyOnceWith({ x: 50 });
  });

  it('saves a shape let go once, and not again when the editor goes away', () => {
    const commit = vi.fn();
    render(commit, vi.fn());

    act(() => editing.change({ x: 50 }));
    editing.release();
    act(() => root.unmount());

    expect(commit).toHaveBeenCalledExactlyOnceWith({ x: 50 });
  });

  it('leaves a shape moved by the keys to the delayed save when the editor goes away', () => {
    const commit = vi.fn();
    const commitLater = vi.fn();
    render(commit, commitLater);

    pressRight();
    act(() => root.unmount());

    expect(commitLater).toHaveBeenCalledExactlyOnceWith({ x: 1 });
    expect(commit).not.toHaveBeenCalled();
  });
});
