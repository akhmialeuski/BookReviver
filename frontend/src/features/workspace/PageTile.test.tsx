import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, row } from '@/features/workspace/fixtures';
import { PageTile } from '@/features/workspace/PageTile';
import { joinRows } from '@/features/workspace/strip';

/** The tile of a page of the strip: the mark of what the page shows stands on its picture, and a reader of a screen hears it. */

const MARK = '[data-testid="strip-content"]';

describe('PageTile', () => {
  let container: HTMLDivElement;
  let root: Root;

  function render(overrides: Parameters<typeof page>[1]): void {
    const [item] = joinRows([page('a', overrides)], []);
    if (item === undefined) {
      throw new Error('The tile has no page.');
    }
    act(() => root.render(<PageTile item={item} highlighted={false} onClick={() => undefined} />));
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('marks a page of text with the mark of text and no word', () => {
    render({ content_type: 'text', content_source: 'kind' });

    const mark = container.querySelector(MARK);
    expect(mark?.getAttribute('data-content')).toBe('text');
    expect(mark?.textContent).toBe('¶');
  });

  it('marks a colour picture and a black-and-white one with the mark of pictures and the colour', () => {
    render({ content_type: 'color-picture', content_source: 'detected' });
    expect(container.querySelector(MARK)?.textContent).toBe('▣Colour');

    render({ content_type: 'bw-picture', content_source: 'detected' });
    expect(container.querySelector(MARK)?.textContent).toBe('▣B/W');
  });

  it('says in the title and to a reader of a screen where the type comes from', () => {
    render({ content_type: 'bw-picture', content_source: 'detected' });

    expect(container.querySelector(MARK)?.getAttribute('title')).toBe(
      'Black-and-white picture · Found by the program',
    );
    const heard = [...container.querySelectorAll('.sr-only')].map((node) => node.textContent);
    expect(heard).toContain('Black-and-white picture · Found by the program');
  });

  it('draws a pencil on a page whose type the reader set, and on no other', () => {
    render({ content_type: 'text', content_source: 'hand' });
    expect(container.querySelector(`${MARK} svg`)).not.toBeNull();
    expect(container.querySelector(MARK)?.getAttribute('data-source')).toBe('hand');

    render({ content_type: 'text', content_source: 'detected' });
    expect(container.querySelector(`${MARK} svg`)).toBeNull();
  });

  it('has marks that are not interactive: the tile is the one button, and a press on a mark is a press on the tile', () => {
    const onClick = vi.fn();
    const marked = [
      { page: page('a', { content_source: 'hand' }), row: row('a', { status: 'failed' }) },
      { page: page('b', { included: false }), row: row('b', { review: 'low-confidence' }) },
    ];

    for (const item of marked) {
      const [tile] = joinRows([item.page], [item.row]);
      if (tile === undefined) {
        throw new Error('The tile has no page.');
      }
      act(() => root.render(<PageTile item={tile} highlighted={false} onClick={onClick} />));

      expect(container.querySelectorAll('button, [role="button"], [tabindex]')).toHaveLength(1);
      for (const mark of container.querySelectorAll(
        '[data-testid="strip-content"], [data-testid="strip-failed"], [data-testid="strip-review"]',
      )) {
        expect(mark.tagName).toBe('SPAN');
        expect(mark.getAttribute('role')).toBeNull();
        expect(mark.getAttribute('tabindex')).toBeNull();
        act(() => mark.dispatchEvent(new MouseEvent('click', { bubbles: true })));
      }
    }

    // Each mark that was pressed has told the tile and nobody else: the content mark and the failed or review mark
    expect(onClick).toHaveBeenCalledTimes(4);
  });
});
