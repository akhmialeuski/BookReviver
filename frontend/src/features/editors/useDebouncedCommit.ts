import { useCallback, useEffect, useRef } from 'react';

/**
 * Hold back a save while the reader is still pressing keys or turning the wheel, and make it once they pause.
 *
 * Every press asks for a save of the latest value, and only the last ask is made, `delay` milliseconds after it. A value
 * still waiting when the editor goes away is saved at once, so a nudge is never lost to a page turn.
 *
 * @param commit Called with the value to save; the latest function passed is the one called.
 * @param delay Milliseconds of quiet after the last ask.
 * @returns The function that asks for a save.
 */
export function useDebouncedCommit<T>(
  commit: (value: T) => void,
  delay: number,
): (value: T) => void {
  const latest = useRef(commit);
  useEffect(() => {
    latest.current = commit;
  });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const waiting = useRef<{ value: T } | null>(null);

  const fire = useCallback((): void => {
    const held = waiting.current;
    waiting.current = null;
    timer.current = null;
    if (held !== null) {
      latest.current(held.value);
    }
  }, []);

  useEffect(
    () => () => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        fire();
      }
    },
    [fire],
  );

  return useCallback(
    (value: T): void => {
      waiting.current = { value };
      if (timer.current !== null) {
        clearTimeout(timer.current);
      }
      timer.current = setTimeout(fire, delay);
    },
    [delay, fire],
  );
}
