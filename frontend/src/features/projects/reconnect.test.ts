import { describe, expect, it, vi } from 'vitest';
import { keepListening, reconnectDelay } from './reconnect';

/**
 * The reconnect loop of the event stream: it opens the stream again after every end, however the stream ended.
 */

async function* endsAtOnce(): AsyncGenerator<never> {
  // no events: the connection closes straight away
}

describe('reconnectDelay', () => {
  it.each([
    [1, 1000],
    [2, 2000],
    [3, 4000],
    [5, 16_000],
    [6, 30_000],
    [50, 30_000],
  ])('waits %d failures for %d ms', (failures, expected) => {
    expect(reconnectDelay(failures)).toBe(expected);
  });
});

describe('keepListening', () => {
  it('opens the stream again after it ends normally, refreshing the data each time', async () => {
    const controller = new AbortController();
    const open = vi.fn(async () => {
      if (open.mock.calls.length >= 3) {
        controller.abort();
      }
      return endsAtOnce();
    });
    const catchUp = vi.fn();

    await keepListening({ open, catchUp, signal: controller.signal, sleep: async () => undefined });

    expect(open).toHaveBeenCalledTimes(3);
    // The last end came after the abort, when nobody wants the data any more
    expect(catchUp).toHaveBeenCalledTimes(2);
  });

  it('opens the stream again after a failed connection', async () => {
    const controller = new AbortController();
    const open = vi.fn(async () => {
      if (open.mock.calls.length >= 2) {
        controller.abort();
        return endsAtOnce();
      }
      throw new TypeError('Failed to fetch');
    });

    await keepListening({
      open,
      catchUp: vi.fn(),
      signal: controller.signal,
      sleep: async () => undefined,
    });

    expect(open).toHaveBeenCalledTimes(2);
  });

  it('waits longer after each connection that gave nothing and starts over after one that delivered', async () => {
    const controller = new AbortController();
    const waits: number[] = [];
    const open = vi.fn(async (onActivity: () => void) => {
      const call = open.mock.calls.length;
      if (call === 4) {
        onActivity();
      }
      if (call >= 5) {
        controller.abort();
      }
      return endsAtOnce();
    });

    await keepListening({
      open,
      catchUp: vi.fn(),
      signal: controller.signal,
      sleep: async (ms) => {
        waits.push(ms);
      },
    });

    expect(waits).toEqual([1000, 2000, 4000, 1000]);
  });

  it('stops without opening anything when it is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    const open = vi.fn(async () => endsAtOnce());

    await keepListening({ open, catchUp: vi.fn(), signal: controller.signal });

    expect(open).not.toHaveBeenCalled();
  });
});
