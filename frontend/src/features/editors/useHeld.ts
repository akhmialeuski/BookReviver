import { useState } from 'react';

/**
 * Keep showing the last value of something while the value that follows it cannot be trusted yet.
 *
 * The versions of a page and the row of its stage are read apart and arrive apart, so for a moment after a run the row
 * names a current version that the list of versions does not hold yet, and whatever is worked out of the two is half of
 * what it was. A value held for that moment is the one the screen already drew, so nothing on it is loaded again.
 *
 * @param key What the value belongs to, such as the page and the stage. A value is never held for another key.
 * @param value The value as it is worked out now, which has to keep its identity between renders while it is the same.
 * @param holding Whether the value of now is to be left out in favour of the last one that was not held.
 * @returns The value of now, or the last value of the same key while `holding`.
 */
export function useHeld<T>(key: string, value: T, holding: boolean): T {
  const [last, setLast] = useState({ key, value });
  if (!holding && (last.key !== key || last.value !== value)) {
    setLast({ key, value });
  }
  return holding && last.key === key ? last.value : value;
}
