import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, row } from '@/features/workspace/fixtures';
import { PageFilter } from '@/features/workspace/params';
import { StageStrip } from '@/features/workspace/StageStrip';
import { countFilters, joinRows } from '@/features/workspace/strip';

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

  function render(length: number): void {
    const pages = Array.from({ length }, (_, index) => page(`p-${index}`, { position: index }));
    const items = joinRows(
      pages,
      pages.map((entry) => row(entry.id)),
    );
    act(() =>
      root.render(
        <StageStrip
          items={items}
          total={length}
          counts={countFilters(items)}
          filter={PageFilter.All}
          currentId={undefined}
          onFilter={vi.fn()}
          onOpen={vi.fn()}
          onGrid={vi.fn()}
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
});
