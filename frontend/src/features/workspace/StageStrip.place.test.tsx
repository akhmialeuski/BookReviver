import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BookPlaceBody, BookPlaceSchema } from '@/api';
import type { PlaceAddress } from '@/features/place/address';
import { PlaceWriterContext } from '@/features/place/PlaceWriterContext';
import { PlaceWriter } from '@/features/place/writer';
import { page, row } from '@/features/workspace/fixtures';
import { PageFilter } from '@/features/workspace/params';
import { StageStrip } from '@/features/workspace/StageStrip';
import { countFilters, joinRows } from '@/features/workspace/strip';

/**
 * The strip keeps the place of the reader: the first page in sight goes to the writer, and a strip that opens at the
 * place of the book scrolls that page to the top.
 *
 * jsdom has no layout, so the height of the scrolling element is given to it, and its `scrollTo`, which jsdom lacks,
 * is recorded.
 */

const BOOK_LENGTH = 1000;
const VIEWPORT_PX = 600;
const ROW_PX = 200;
const ADDRESS: PlaceAddress = { mode: 'workspace', stage: 'geometry' };
const LEFT_AT = 500;

function placeAt(stripPageId: string | null): BookPlaceSchema {
  return {
    mode: 'workspace',
    stage: 'geometry',
    page_id: null,
    scan_id: null,
    source_id: null,
    view: 'page',
    compare: 'off',
    filter: 'all',
    canvas: null,
    strip_page_id: stripPageId,
    updated_at: '2026-10-02T10:00:00Z',
  };
}

describe('StageStrip and the place of the book', () => {
  let container: HTMLDivElement;
  let root: Root;
  let scrollTo: ReturnType<typeof vi.fn>;
  let sent: BookPlaceBody[];

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(VIEWPORT_PX);
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(300);
    // The virtualizer takes the end of the list from the height of the content, which jsdom does not lay out
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(BOOK_LENGTH * ROW_PX);
    scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      value: scrollTo,
      configurable: true,
      writable: true,
    });
    sent = [];
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo');
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function render(initial: BookPlaceSchema | null): PlaceWriter {
    const writer = new PlaceWriter({
      address: ADDRESS,
      initial,
      send: (body) => {
        sent.push(body);
        return Promise.resolve();
      },
      remember: () => undefined,
    });
    const pages = Array.from({ length: BOOK_LENGTH }, (_, index) =>
      page(`p-${index}`, { position: index }),
    );
    const items = joinRows(
      pages,
      pages.map((entry) => row(entry.id)),
    );
    act(() =>
      root.render(
        <PlaceWriterContext value={writer}>
          <StageStrip
            items={items}
            total={BOOK_LENGTH}
            counts={countFilters(items)}
            filter={PageFilter.All}
            currentId={undefined}
            onFilter={vi.fn()}
            onOpen={vi.fn()}
            onGrid={vi.fn()}
          />
        </PlaceWriterContext>,
      ),
    );
    return writer;
  }

  it('reports the first page in sight after the reader scrolls', () => {
    const writer = render(null);
    const scroller = container.querySelector<HTMLElement>('[data-testid="strip-scroll"]');
    expect(scroller).not.toBeNull();

    act(() => {
      if (scroller !== null) {
        scroller.scrollTop = 40 * ROW_PX;
        scroller.dispatchEvent(new Event('scroll'));
      }
    });
    writer.flush(false);

    // The rows in sight are measured at the height jsdom gives every element, so the page is found from the list
    const reported = sent[0]?.strip_page_id ?? null;
    const first = Number(reported?.replace('p-', ''));
    expect(sent).toHaveLength(1);
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(40);
  });

  it('scrolls the page the place names to the top when the strip opens at the place', () => {
    render(placeAt(`p-${LEFT_AT}`));

    // Every row is at least the estimated height, so the row of that page starts at least this far down
    const tops = scrollTo.mock.calls.map(([options]) => (options as { top: number }).top);
    expect(Math.max(...tops)).toBeGreaterThanOrEqual(LEFT_AT * ROW_PX);
  });

  it('stays at the top when the place names a page the list does not hold', () => {
    render(placeAt('p-deleted'));

    const tops = scrollTo.mock.calls.map(([options]) => (options as { top: number }).top);
    expect(Math.max(0, ...tops)).toBe(0);
  });
});
