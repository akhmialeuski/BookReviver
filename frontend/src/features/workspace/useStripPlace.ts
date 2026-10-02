import type { Virtualizer } from '@tanstack/react-virtual';
import { type RefObject, useEffect, useRef } from 'react';
import { usePlaceWriter } from '@/features/place/PlaceWriterContext';
import { Restore } from '@/features/place/writer';

/**
 * Keeps the place of a virtual list of pages, the strip or the grid: which page is first in sight.
 *
 * The scroll position in pixels depends on the width of the list and the size of its rows, which differ on another
 * device, so the place keeps the first page in sight instead. The list tells the writer when it scrolls, answers with
 * its first page when the writer asks, and scrolls that page to the top once when the screen opens at the place.
 *
 * @param scroller The element that scrolls.
 * @param virtualizer The virtualizer of the rows.
 * @param pageIds The identifiers of the pages in the order they are listed.
 * @param perRow How many pages stand in a row, 1 for the strip.
 * @param ready Whether the list is on screen and laid out, so a row can be scrolled to; an empty list has no scroller,
 * and the grid knows its width only after it mounts.
 */
export function useStripPlace(
  scroller: RefObject<HTMLElement | null>,
  virtualizer: Virtualizer<HTMLDivElement, Element>,
  pageIds: readonly string[],
  perRow: number,
  ready = true,
): void {
  const place = usePlaceWriter();
  const latest = useRef({ pageIds, perRow });
  const restored = useRef(false);

  useEffect(() => {
    latest.current = { pageIds, perRow };
  });

  useEffect(() => {
    const target = scroller.current;
    if (place === null || target === null || !ready) {
      return;
    }
    const onScroll = (): void => place.touch();
    target.addEventListener('scroll', onScroll, { passive: true });
    const detach = place.attachStrip(() => {
      const row = virtualizer.range?.startIndex;
      return row === undefined
        ? null
        : (latest.current.pageIds[row * latest.current.perRow] ?? null);
    });
    return () => {
      detach();
      target.removeEventListener('scroll', onScroll);
    };
  }, [place, scroller, virtualizer, ready]);

  useEffect(() => {
    if (place === null || !ready || restored.current) {
      return;
    }
    restored.current = true;
    const pageId = place.takeRestore(Restore.Strip);
    const index = pageId === null ? -1 : latest.current.pageIds.indexOf(pageId);
    if (index >= 0) {
      virtualizer.scrollToIndex(Math.floor(index / latest.current.perRow), { align: 'start' });
    }
  }, [place, ready, virtualizer]);
}
