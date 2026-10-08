import { type DebouncedFunc, debounce } from 'lodash-es';
import { useEffect, useMemo, useRef } from 'react';

/**
 * Wait until calls stop coming, and then make the latest of them once.
 *
 * The debounced function is made once per delay, and it calls the callback of the latest render with the arguments of the
 * latest call. The callback may therefore close over what is fresh in the component, but whatever the waiting call has to
 * act on, such as the page or the record it was asked for, has to travel in its arguments: a closure is read when the
 * delay ends, and by then the component may stand for another page. What is still waiting when the component goes away is
 * either made at once or dropped.
 */

/** What becomes of a call that is still waiting when the component goes away. */
export interface DebouncedCallbackOptions {
  /** True to make the waiting call at the unmount, false to drop it. */
  readonly flushOnUnmount: boolean;
}

/**
 * Debounce a callback for the lifetime of a component.
 *
 * @param callback Called with the arguments of the latest call; the latest function passed is the one called, so it must
 *   not be what tells the page or the record the call is for.
 * @param waitMs Milliseconds of quiet after the last call.
 * @param options What becomes of a waiting call at the unmount.
 * @returns The debounced function, with `flush` to make the waiting call now and `cancel` to drop it.
 */
export function useDebouncedCallback<ArgsT extends unknown[]>(
  callback: (...args: ArgsT) => void,
  waitMs: number,
  { flushOnUnmount }: DebouncedCallbackOptions,
): DebouncedFunc<(...args: ArgsT) => void> {
  const latest = useRef(callback);
  useEffect(() => {
    latest.current = callback;
  });
  const debounced = useMemo(
    () => debounce((...args: ArgsT) => latest.current(...args), waitMs),
    [waitMs],
  );
  useEffect(
    () => () => {
      if (flushOnUnmount) {
        debounced.flush();
      } else {
        debounced.cancel();
      }
    },
    [debounced, flushOnUnmount],
  );
  return debounced;
}
