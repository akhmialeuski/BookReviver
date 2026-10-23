import { type DebouncedFunc, throttle } from 'lodash-es';

/**
 * Folds a burst of requests for the same work into a run at its start and one at its end.
 *
 * A run of a stage over a hundred pages sends an event per page, and reading the summary and the rows again after
 * each would cost more requests than the run has pages. The first request of a key runs the work at once, so a single
 * change shows without delay, and starts a wait. The requests that arrive during the wait are absorbed, and the work
 * runs once more when it ends, so it sees the result of the last event.
 */

export class Coalescer {
  // One throttle per key, kept for the life of the page: a key names a part of a book, so there are few of them, and
  // a throttle dropped after its run would start every request of a burst at once
  private readonly runners = new Map<string, DebouncedFunc<(work: () => void) => void>>();
  private readonly delayMs: number;

  /**
   * @param delayMs How long a burst of one key is folded after the request that started it.
   */
  constructor(delayMs: number) {
    this.delayMs = delayMs;
  }

  /**
   * Run some work now, or at the end of the wait when a request of the same key ran it a moment ago.
   *
   * @param key What the work is for. Requests with the same key during one wait run the work once at its end.
   * @param work The work of the request. Requests of one key are expected to carry the same work.
   */
  schedule(key: string, work: () => void): void {
    let runner = this.runners.get(key);
    if (runner === undefined) {
      runner = throttle((run: () => void) => run(), this.delayMs, {
        leading: true,
        trailing: true,
      });
      this.runners.set(key, runner);
    }
    runner(work);
  }
}
