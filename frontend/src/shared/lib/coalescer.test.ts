import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Coalescer } from '@/shared/lib/coalescer';

describe('Coalescer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs the work of a single request at once and not again', () => {
    const coalescer = new Coalescer(400);
    const work = vi.fn();

    coalescer.schedule('stage', work);
    expect(work).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(400);
    expect(work).toHaveBeenCalledTimes(1);
  });

  it('runs the work of a burst at its start and once at the end of the wait, however many requests came', () => {
    const coalescer = new Coalescer(400);
    const work = vi.fn();

    for (let request = 0; request < 50; request += 1) {
      coalescer.schedule('stage', work);
    }
    expect(work).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(399);
    expect(work).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(work).toHaveBeenCalledTimes(2);
  });

  it('keeps the work of different keys apart', () => {
    const coalescer = new Coalescer(400);
    const first = vi.fn();
    const second = vi.fn();

    coalescer.schedule('geometry', first);
    coalescer.schedule('geometry', first);
    coalescer.schedule('cleanup', second);
    vi.advanceTimersByTime(400);

    expect(first).toHaveBeenCalledTimes(2);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('runs a request that comes after a quiet wait at once again', () => {
    const coalescer = new Coalescer(400);
    const work = vi.fn();

    coalescer.schedule('stage', work);
    vi.advanceTimersByTime(400);
    coalescer.schedule('stage', work);

    expect(work).toHaveBeenCalledTimes(2);
  });
});
