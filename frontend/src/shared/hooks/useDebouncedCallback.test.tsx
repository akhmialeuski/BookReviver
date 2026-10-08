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

  function Probe({
    flushOnUnmount,
    handler,
  }: {
    flushOnUnmount: boolean;
    handler: (value: number) => void;
  }): null {
    ask = useDebouncedCallback(handler, DELAY_MS, { flushOnUnmount });
    return null;
  }

  function mount(flushOnUnmount: boolean, handler: (value: number) => void = callback): void {
    container = document.createElement('div');
    root = createRoot(container);
    act(() => root.render(<Probe flushOnUnmount={flushOnUnmount} handler={handler} />));
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

  it('makes a waiting call with the callback of the latest render and the arguments of the call', () => {
    const forFirstPage = vi.fn();
    const forSecondPage = vi.fn();
    mount(true, forFirstPage);
    ask(7);

    act(() => root.render(<Probe flushOnUnmount handler={forSecondPage} />));
    vi.advanceTimersByTime(DELAY_MS);

    // What a call has to act on travels in its arguments, since the closure it was asked with is not the one that runs
    expect(forFirstPage).not.toHaveBeenCalled();
    expect(forSecondPage).toHaveBeenCalledExactlyOnceWith(7);
    act(() => root.unmount());
  });

  it('calls nothing when nothing was asked', () => {
    mount(true);

    vi.advanceTimersByTime(DELAY_MS * 2);
    act(() => root.unmount());

    expect(callback).not.toHaveBeenCalled();
  });
});
