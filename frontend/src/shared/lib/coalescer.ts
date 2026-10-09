import { type DebouncedFunc, throttle } from 'lodash-es';

/**
 * Folds a burst of requests for the same work into one run a short while after the first of them.
 *
 * A run of a stage over a hundred pages sends an event per page, and reading the summary and the rows again after
 * each would cost more requests than the run has pages. The first request of a key starts a wait, the requests that
 * arrive during it are absorbed, and the work runs once when the wait ends, so it sees the result of the last event.
 */

export class Coalescer {
  private readonly runners = new Map<string, DebouncedFunc<(work: () => void) => void>>();
  private readonly delayMs: number;

  /**
   * @param delayMs How long the first request of a key waits before the work runs.
   */
  constructor(delayMs: number) {
    this.delayMs = delayMs;
  }

  /**
   * Ask for some work to run soon.
   *
   * @param key What the work is for. Requests with the same key during one wait run the work once.
   * @param work The work of the request. Requests of one key are expected to carry the same work.
   */
  schedule(key: string, work: () => void): void {
    let runner = this.runners.get(key);
    if (runner === undefined) {
      // A throttle that skips the leading edge runs once at the end of the wait that the first call started
      runner = throttle(
        (run: () => void) => {
          this.runners.delete(key);
          run();
        },
        this.delayMs,
        { leading: false },
      );
      this.runners.set(key, runner);
    }
    runner(work);
  }
}
