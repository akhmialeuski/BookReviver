import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, row } from '@/features/workspace/fixtures';
import { PageFilter } from '@/features/workspace/params';
import { StageStrip } from '@/features/workspace/StageStrip';
import {
  countFilters,
  joinRows,
  type StripItem,
  type VariantView,
} from '@/features/workspace/strip';

/**
 * The strip of a long book keeps only the rows in sight in the document.
 *
 * jsdom has no layout, so the height of the scrolling element is given to it, as a browser would measure it.
 */

const BOOK_LENGTH = 1000;
const VIEWPORT_PX = 600;
// Rows in sight at the estimated height of 200 px, with the rows kept around them on either side
const MOST_ROWS_IN_DOCUMENT = 20;

describe('StageStrip', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(VIEWPORT_PX);
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(300);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function render(
    length: number,
    extra: {
      filter?: PageFilter;
      reasonOf?: (item: StripItem) => string | null;
      withWide?: boolean;
      variants?: VariantView;
    } = {},
  ): void {
    const pages = Array.from({ length }, (_, index) => page(`p-${index}`, { position: index }));
    const items = joinRows(
      pages,
      pages.map((entry) => row(entry.id, { status: 'stale' })),
    );
    act(() =>
      root.render(
        <StageStrip
          items={items}
          total={length}
          counts={countFilters(items)}
          filter={extra.filter ?? PageFilter.All}
          currentId={undefined}
          onFilter={vi.fn()}
          onOpen={vi.fn()}
          onGrid={vi.fn()}
          reasonOf={extra.reasonOf}
          withWide={extra.withWide}
          variants={extra.variants}
        />,
      ),
    );
  }

  it('draws only the tiles in sight for a book of a thousand pages', () => {
    render(BOOK_LENGTH);

    const tiles = container.querySelectorAll('[data-testid="strip-page"]');
    expect(tiles.length).toBeGreaterThan(0);
    expect(tiles.length).toBeLessThanOrEqual(MOST_ROWS_IN_DOCUMENT);
    expect(container.querySelector('[data-testid="strip-count"]')?.textContent).toBe(
      String(BOOK_LENGTH),
    );
  });

  it('starts at the first page of the book', () => {
    render(BOOK_LENGTH);

    const first = container.querySelector('[data-testid="strip-page"]');
    expect(first?.getAttribute('data-page-id')).toBe('p-0');
  });

  it('draws every tile of a short book', () => {
    render(3);

    expect(container.querySelectorAll('[data-testid="strip-page"]')).toHaveLength(3);
  });

  it('writes the reason under each page when the Check filter is on', () => {
    render(2, { filter: PageFilter.Check, reasonOf: (item) => `Reason of ${item.page.id}` });

    const reasons = [...container.querySelectorAll('[data-testid="strip-reason"]')];
    expect(reasons.map((reason) => reason.textContent)).toEqual(['Reason of p-0', 'Reason of p-1']);
  });

  it('writes no reason under a page when another filter is on', () => {
    render(2, { filter: PageFilter.All, reasonOf: () => 'A reason' });

    expect(container.querySelector('[data-testid="strip-reason"]')).toBeNull();
  });

  it('writes no reason for a page the stage has none for', () => {
    render(2, { filter: PageFilter.Check, reasonOf: () => null });

    expect(container.querySelector('[data-testid="strip-reason"]')).toBeNull();
  });

  describe('the variants of the stage', () => {
    const PLATES_MARK = { name: 'Plates', tone: 'bg-variant-2', pinned: true };

    function variantView(overrides: Partial<VariantView> = {}): VariantView {
      return {
        markOf: (item) => (item.page.id === 'p-0' ? PLATES_MARK : null),
        options: [
          { id: 'text', name: 'Text', pages: 1 },
          { id: 'plates', name: 'Plates', pages: 1 },
        ],
        selected: null,
        onSelect: vi.fn(),
        ...overrides,
      };
    }

    it('marks the page with its variant, and says in words that it is pinned', () => {
      render(2, { variants: variantView() });

      const marks = container.querySelectorAll('[data-testid="strip-variant"]');
      expect(marks).toHaveLength(1);
      expect(marks[0]?.getAttribute('data-variant')).toBe('Plates');
      expect(marks[0]?.getAttribute('data-pinned')).toBe('true');
      expect(container.querySelector('[data-testid="strip-page"]')?.textContent).toContain(
        'Plates · pinned',
      );
    });

    it('offers the variants to narrow the pages to, with the pages each made', () => {
      render(2, { variants: variantView() });

      const select = container.querySelector<HTMLSelectElement>(
        '[data-testid="strip-variant-filter"]',
      );
      expect([...(select?.options ?? [])].map((option) => option.textContent)).toEqual([
        'All variants',
        'Text · 1',
        'Plates · 1',
      ]);
    });

    it('tells the screen which variant was chosen, and that all of them were', () => {
      const onSelect = vi.fn();
      render(2, { variants: variantView({ onSelect }) });
      const select = container.querySelector<HTMLSelectElement>(
        '[data-testid="strip-variant-filter"]',
      );

      act(() => {
        if (select !== null) {
          select.value = 'plates';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
      act(() => {
        if (select !== null) {
          select.value = '';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });

      expect(onSelect.mock.calls).toEqual([['plates'], [null]]);
    });

    it('shows no mark and no choice for a stage with a single recipe', () => {
      render(2);

      expect(container.querySelector('[data-testid="strip-variant"]')).toBeNull();
      expect(container.querySelector('[data-testid="strip-variant-filter"]')).toBeNull();
    });
  });

  it('offers the Marked bad filter with the number of pages it lists', () => {
    render(2);

    expect(container.querySelector('[data-testid="strip-filter-bad"]')?.textContent).toBe(
      'Marked bad 0',
    );
  });

  it('marks the thumbnail of a page whose result is marked bad, and no other', () => {
    const pages = [page('p-0', { position: 0 }), page('p-1', { position: 1 })];
    const items = joinRows(pages, [row('p-0', { marked_bad: true }), row('p-1')]);
    act(() =>
      root.render(
        <StageStrip
          items={items}
          total={2}
          counts={countFilters(items)}
          filter={PageFilter.All}
          currentId={undefined}
          onFilter={vi.fn()}
          onOpen={vi.fn()}
          onGrid={vi.fn()}
        />,
      ),
    );

    const marked = [...container.querySelectorAll('[data-testid="strip-page"]')].filter(
      (tile) => tile.querySelector('[data-testid="strip-marked-bad"]') !== null,
    );
    expect(marked.map((tile) => tile.getAttribute('data-page-id'))).toEqual(['p-0']);
    expect(container.querySelector('[data-testid="strip-filter-bad"]')?.textContent).toBe(
      'Marked bad 1',
    );
  });

  it('offers the Wide filter only when it is asked for', () => {
    render(2);
    expect(container.querySelector('[data-testid="strip-filter-wide"]')).toBeNull();

    render(2, { withWide: true });
    expect(container.querySelector('[data-testid="strip-filter-wide"]')?.textContent).toBe(
      'Wide 0',
    );
  });
});
