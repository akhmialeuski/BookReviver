import { useSyncExternalStore } from 'react';

/**
 * Whether the window matches a CSS media query, kept current as the window is resized.
 *
 * The match is an external source that React does not own, so the hook subscribes to it with `useSyncExternalStore`, as
 * the React documentation advises, and needs no library. A browser that has no `matchMedia`, such as the test
 * environment, never matches.
 */

/** Where the narrow layouts begin, equal to the `lg` breakpoint of Tailwind (64rem). */
export const WIDE_MIN_WIDTH_PX = 1024;

/** The query of a window narrower than the `lg` breakpoint. */
export const NARROW_QUERY = `(width < ${WIDE_MIN_WIDTH_PX}px)`;

export function useMediaQuery(query: string): boolean {
  const subscribe = (notify: () => void): (() => void) => {
    if (typeof window.matchMedia !== 'function') {
      return () => undefined;
    }
    const list = window.matchMedia(query);
    list.addEventListener('change', notify);
    return () => list.removeEventListener('change', notify);
  };
  const read = (): boolean =>
    typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
  return useSyncExternalStore(subscribe, read, () => false);
}

/** Whether the window is narrower than the `lg` breakpoint, where the workspace gives the canvas the whole width. */
export function useIsNarrow(): boolean {
  return useMediaQuery(NARROW_QUERY);
}
