import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, row } from '@/features/workspace/fixtures';
import { PageFilter } from '@/features/workspace/params';
import { StageStrip } from '@/features/workspace/StageStrip';
import {
  countFilters,
  type FlagView,
  joinRows,
  type KindView,
  type StopView,
  type StripItem,
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
      kinds?: KindView;
      flagged?: FlagView;
      stopped?: StopView;
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
          kinds={extra.kinds}
          flagged={extra.flagged}
          stopped={extra.stopped}
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

  describe('the steps a run stopped at', () => {
    it('names each step by its title, with the pages that stopped there and no number', () => {
      const titles = ['Perspective', 'Deskew'];
      render(2, {
        stopped: {
          options: [
            { step: 0, pages: 1 },
            { step: 1, pages: 76 },
          ],
          titleOf: (step) => titles[step] ?? '',
          selected: null,
          onSelect: vi.fn(),
        },
      });

      const options = container.querySelectorAll<HTMLOptionElement>(
        '[data-testid="strip-stopped-filter"] option',
      );
      expect([...options].map((option) => option.textContent)).toEqual([
        'Any step',
        'Stopped at Perspective · 1',
        'Stopped at Deskew · 76',
      ]);
    });
  });

  describe('the kinds of page of the stage', () => {
    function kindView(overrides: Partial<KindView> = {}): KindView {
      return {
        options: [
          { kind: 'text', pages: 1 },
          { kind: 'color-picture', pages: 1 },
        ],
        selected: null,
        onSelect: vi.fn(),
        ...overrides,
      };
    }

    const filter = (): HTMLSelectElement | null =>
      container.querySelector<HTMLSelectElement>('[data-testid="strip-kind-filter"]');

    it('offers the kinds to narrow the pages to, with the pages of each', () => {
      render(2, { kinds: kindView() });

      expect([...(filter()?.options ?? [])].map((option) => option.textContent)).toEqual([
        'All kinds',
        'Text · 1',
        'Colour picture · 1',
      ]);
    });

    it('tells the screen which kind was chosen, and that all of them were', () => {
      const onSelect = vi.fn();
      render(2, { kinds: kindView({ onSelect }) });
      const select = filter();

      act(() => {
        if (select !== null) {
          select.value = 'color-picture';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
      act(() => {
        if (select !== null) {
          select.value = '';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });

      expect(onSelect.mock.calls).toEqual([['color-picture'], [null]]);
    });

    it('shows no choice for a stage with pages of a single kind', () => {
      render(2);

      expect(filter()).toBeNull();
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

  describe('the flags of the pages at the open step', () => {
    function flagView(overrides: Partial<FlagView> = {}): FlagView {
      return {
        options: [
          { flag: 'unsure', pages: 2 },
          { flag: 'unusual', pages: 0 },
          { flag: 'by-hand', pages: 1 },
          { flag: 'skipped', pages: 3 },
        ],
        selected: null,
        onSelect: vi.fn(),
        ...overrides,
      };
    }

    const select = (): HTMLSelectElement | null =>
      container.querySelector<HTMLSelectElement>('[data-testid="strip-step-filter"]');

    it('offers every reason a page asks for a look, with the pages that carry it', () => {
      render(2, { flagged: flagView() });

      expect([...(select()?.options ?? [])].map((option) => option.textContent)).toEqual([
        'Any page',
        'Step unsure · 2',
        'Differs from the book · 0',
        'Set by hand · 1',
        'Skipped: a leaf the program drew · 3',
      ]);
    });

    it('tells the screen which flag was chosen, and that every page was', () => {
      const onSelect = vi.fn();
      render(2, { flagged: flagView({ onSelect }) });
      const choose = (value: string): void => {
        act(() => {
          const element = select();
          if (element !== null) {
            element.value = value;
            element.dispatchEvent(new Event('change', { bubbles: true }));
          }
        });
      };

      choose('by-hand');
      choose('');

      expect(onSelect.mock.calls).toEqual([['by-hand'], [null]]);
    });

    it('shows the chosen flag', () => {
      render(2, { flagged: flagView({ selected: 'unusual' }) });

      expect(select()?.value).toBe('unusual');
    });

    it('says no page carries the reason, and not that the book has no pages, when the flag lists none', () => {
      render(0, { flagged: flagView({ selected: 'by-hand' }) });

      expect(container.textContent).toContain('No page of the book carries this reason');
      expect(container.textContent).not.toContain('This book has no pages yet');
    });

    it('is not on the strip when no step is open', () => {
      render(2);

      expect(select()).toBeNull();
    });
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
