/**
 * Keeping a server-sent events stream open for as long as a page needs it.
 *
 * A stream ends in more than one way that is not the page leaving: the server restarts, a proxy closes an idle
 * connection, or the connection fails. The generated client ends its stream normally in all of these, so the end of
 * the stream says nothing about whether anyone still wants it. This loop opens the stream again until it is aborted,
 * with a delay that grows while connections give nothing and starts over once one delivers an event, and refreshes the
 * data after every end, because the events of the gap are lost.
 */

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30_000;

/** Return how long to wait before opening the stream again after `failures` connections in a row that gave nothing. */
export function reconnectDelay(failures: number): number {
  return Math.min(BASE_DELAY_MS * 2 ** Math.max(failures - 1, 0), MAX_DELAY_MS);
}

/** Wait `ms` milliseconds, or less when the signal is aborted. */
export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export interface ReconnectOptions {
  /** Open the stream once; `onActivity` is to be called for every event it delivers. */
  open: (onActivity: () => void) => Promise<AsyncIterable<unknown>>;
  /** Refresh what the lost events would have changed; called after every end of the stream. */
  catchUp: () => void;
  signal: AbortSignal;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

/** Read the stream, and open it again whenever it ends, until the signal is aborted. */
export async function keepListening({
  open,
  catchUp,
  signal,
  sleep = abortableSleep,
}: ReconnectOptions): Promise<void> {
  let failures = 0;
  while (!signal.aborted) {
    let active = false;
    try {
      const stream = await open(() => {
        active = true;
      });
      // The events reach the handler while the stream is read, so reading it is the whole job
      for await (const _event of stream) {
        // intentionally empty
      }
    } catch {
      // A failed connection is handled like an ended one
    }
    if (signal.aborted) {
      return;
    }
    failures = active ? 1 : failures + 1;
    catchUp();
    await sleep(reconnectDelay(failures), signal);
  }
}
