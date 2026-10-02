import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';

/** A save held back until the reader pauses, and made at once when the editor goes away. */

const DELAY_MS = 500;

describe('useDebouncedCommit', () => {
  let container: HTMLDivElement;
  let root: Root;
  let ask: (value: number) => void;
  const commit = vi.fn();

  function Probe(): null {
    ask = useDebouncedCommit(commit, DELAY_MS);
    return null;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    commit.mockReset();
    container = document.createElement('div');
    root = createRoot(container);
    act(() => root.render(<Probe />));
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('saves only the last value, once the reader pauses', () => {
    ask(1);
    vi.advanceTimersByTime(DELAY_MS - 1);
    ask(2);
    vi.advanceTimersByTime(DELAY_MS - 1);
    expect(commit).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);

    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith(2);
  });

  it('saves a value that is still waiting when the editor goes away', () => {
    ask(7);

    act(() => root.unmount());

    expect(commit).toHaveBeenCalledWith(7);
    root = createRoot(container);
  });

  it('saves nothing when nothing was asked', () => {
    vi.advanceTimersByTime(DELAY_MS * 2);
    act(() => root.unmount());

    expect(commit).not.toHaveBeenCalled();
    root = createRoot(container);
  });
});
