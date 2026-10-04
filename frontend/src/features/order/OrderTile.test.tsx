import { DndContext } from '@dnd-kit/core';
import { SortableContext } from '@dnd-kit/sortable';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema } from '@/api';
import { OrderTile, type TileClick } from '@/features/order/OrderTile';
import { SECTION_TONES, type SectionSpan, sectionSpans } from '@/features/order/sections';
import { AnchorSide } from '@/features/pages/order';
import { page, section } from '@/features/workspace/fixtures';

/**
 * The tile of the Order grid: what a click with a modifier key asks of the selection, the words and marks it shows
 * for a page, the numbers of a preview, and the ring of the colour of the section round the number.
 */

describe('OrderTile', () => {
  let container: HTMLDivElement;
  let root: Root;
  let clicks: TileClick[];

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    clicks = [];
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function render(
    entry: PageSchema,
    options: {
      selected?: boolean;
      dropSide?: AnchorSide | null;
      previewLabel?: string;
      span?: SectionSpan;
    } = {},
  ): HTMLButtonElement {
    act(() =>
      root.render(
        <DndContext>
          <SortableContext items={[entry.id]}>
            <OrderTile
              page={entry}
              selected={options.selected ?? false}
              dropSide={options.dropSide ?? null}
              previewLabel={options.previewLabel}
              section={options.span}
              onSelect={(click) => clicks.push(click)}
              onOpen={() => undefined}
            />
          </SortableContext>
        </DndContext>,
      ),
    );
    const tile = container.querySelector<HTMLButtonElement>('[data-testid="order-tile"]');
    if (tile === null) {
      throw new Error('The tile is not drawn');
    }
    return tile;
  }

  function click(tile: HTMLElement, init: MouseEventInit = {}): void {
    act(() => {
      tile.dispatchEvent(new MouseEvent('click', { bubbles: true, ...init }));
    });
  }

  it('asks to select the page on a plain click, and to extend or add with Shift and Ctrl', () => {
    const tile = render(page('a', { label: '12' }));
    click(tile);
    click(tile, { shiftKey: true });
    click(tile, { ctrlKey: true });
    click(tile, { metaKey: true });
    expect(clicks).toEqual([
      { range: false, toggle: false },
      { range: true, toggle: false },
      { range: false, toggle: true },
      { range: false, toggle: true },
    ]);
  });

  it('does not select the page when the click is the one a browser sends after Space lifted it', () => {
    const tile = render(page('a'));
    act(() => {
      tile.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', bubbles: true }));
    });
    click(tile, { detail: 0 });
    expect(clicks).toEqual([]);

    // Enter on a button is a click with no detail too, and that one selects
    act(() => {
      tile.dispatchEvent(new KeyboardEvent('keydown', { code: 'Enter', bubbles: true }));
    });
    click(tile, { detail: 0 });
    expect(clicks).toHaveLength(1);
  });

  it('shows the printed number, the kind and the place in the book', () => {
    const tile = render(page('a', { label: '44', kind: 'plate', position: 45 }));
    expect(tile.textContent).toContain('p. 44');
    expect(tile.textContent).toContain('Plate');
    expect(tile.textContent).toContain('#46');
    expect(tile.getAttribute('aria-pressed')).toBe('false');
  });

  it('says that a page has no number, and that a page left out of the book is', () => {
    const tile = render(page('a', { included: false }));
    expect(tile.textContent).toContain('no number');
    expect(tile.textContent).toContain('Left out of the book');
  });

  it('draws a missing page as a place waiting for a scan', () => {
    const tile = render(page('a', { origin: 'placeholder', images: null, label: '51' }));
    expect(tile.textContent).toContain('Missing page');
    expect(tile.querySelector('img')).toBeNull();
    expect(tile.getAttribute('data-check')).toBe('true');
  });

  it('marks the selected page', () => {
    const tile = render(page('a'), { selected: true });
    expect(tile.getAttribute('aria-pressed')).toBe('true');
    expect(tile.getAttribute('data-selected')).toBe('true');
  });

  it('draws the bar of a drop on the side the pages would land on, and no bar otherwise', () => {
    expect(render(page('a')).querySelector('[data-testid="drop-bar"]')).toBeNull();
    const before = render(page('a'), { dropSide: AnchorSide.Before });
    expect(before.querySelector('[data-testid="drop-bar"]')?.getAttribute('data-side')).toBe(
      'before',
    );
    const after = render(page('a'), { dropSide: AnchorSide.After });
    expect(after.querySelector('[data-testid="drop-bar"]')?.getAttribute('data-side')).toBe(
      'after',
    );
  });

  it('strikes the old number out and shows the new one while a numbering is previewed', () => {
    const tile = render(page('a', { label: '44' }), { previewLabel: '10' });
    expect(tile.querySelector('s')?.textContent).toBe('44');
    expect(tile.querySelector('[data-testid="new-number"]')?.textContent).toBe('10');
  });

  it('shows "no number" struck out for a page with no number that gets one', () => {
    const tile = render(page('a'), { previewLabel: '1' });
    expect(tile.querySelector('[data-testid="old-number"]')?.textContent).toBe('no number');
    expect(tile.querySelector('[data-testid="new-number"]')?.textContent).toBe('1');
  });

  it('shows the number as it is when the preview gives the same number', () => {
    const tile = render(page('a', { label: '7' }), { previewLabel: '7' });
    expect(tile.querySelector('[data-testid="new-number"]')).toBeNull();
    expect(tile.textContent).toContain('p. 7');
  });

  it('shows an erased number as "no number" in blue', () => {
    const tile = render(page('a', { label: '7' }), { previewLabel: '' });
    expect(tile.querySelector('[data-testid="new-number"]')?.textContent).toBe('no number');
  });

  describe('the section of the page', () => {
    const entry = page('a', { label: 'vi', section_id: 's1' });
    const [span] = sectionSpans([entry], [section('s1', 'a', { name: 'Preface' })]);

    it('rings the number in the colour of the section and names the section in its title', () => {
      const tile = render(entry, { span });
      const number = tile.querySelector('[data-testid="page-number"]');
      expect(number?.className).toContain(SECTION_TONES[0]?.ring);
      expect(number?.getAttribute('title')).toBe('Section: Preface');
      expect(tile.getAttribute('data-section-id')).toBe('s1');
    });

    it('draws no colour for a page that is in no section', () => {
      const tile = render(entry);
      const number = tile.querySelector('[data-testid="page-number"]');
      expect(number?.className).toContain('border-transparent');
      expect(number?.hasAttribute('title')).toBe(false);
      expect(tile.hasAttribute('data-section-id')).toBe(false);
    });

    it('marks a number written by hand with a pencil and says so to a screen reader', () => {
      const tile = render(page('a', { label: '12a', label_manual: true }));
      const number = tile.querySelector('[data-testid="page-number"]');
      expect(number?.getAttribute('data-manual')).toBe('true');
      expect(number?.querySelector('svg')).not.toBeNull();
      expect(number?.textContent).toContain('Number set by hand');
    });

    it('marks no pencil on a number that a section wrote', () => {
      const tile = render(entry);
      expect(tile.querySelector('[data-testid="page-number"]')?.hasAttribute('data-manual')).toBe(
        false,
      );
      expect(tile.querySelector('svg')).toBeNull();
    });
  });
});
