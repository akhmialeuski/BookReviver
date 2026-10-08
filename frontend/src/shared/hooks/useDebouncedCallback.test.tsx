import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDebouncedCallback } from '@/shared/hooks/useDebouncedCallback';

/** A call held back until the calls pause, and either made or dropped when the component goes away. */

const DELAY_MS = 500;

describe('useDebouncedCallback', () => {
  let container: HTMLDivElement;
  let root: Root;
  let ask: (value: number) => void;
  const callback = vi.fn();

  function Probe({ flushOnUnmount }: { flushOnUnmount: boolean }): null {
    ask = useDebouncedCallback(callback, DELAY_MS, { flushOnUnmount });
    return null;
  }

  function mount(flushOnUnmount: boolean): void {
    container = document.createElement('div');
    root = createRoot(container);
    act(() => root.render(<Probe flushOnUnmount={flushOnUnmount} />));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    callback.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('calls with the last value only, once the calls pause', () => {
    mount(true);

    ask(1);
    vi.advanceTimersByTime(DELAY_MS - 1);
    ask(2);
    vi.advanceTimersByTime(DELAY_MS - 1);
    expect(callback).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);

    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenCalledWith(2);
    act(() => root.unmount());
  });

  it('makes a waiting call at the unmount when asked to flush', () => {
    mount(true);
    ask(7);

    act(() => root.unmount());

    expect(callback).toHaveBeenCalledExactlyOnceWith(7);
  });

  it('drops a waiting call at the unmount when asked to cancel', () => {
    mount(false);
    ask(7);

    act(() => root.unmount());
    vi.advanceTimersByTime(DELAY_MS * 2);

    expect(callback).not.toHaveBeenCalled();
  });

  it('calls nothing when nothing was asked', () => {
    mount(true);

    vi.advanceTimersByTime(DELAY_MS * 2);
    act(() => root.unmount());

    expect(callback).not.toHaveBeenCalled();
  });
});
