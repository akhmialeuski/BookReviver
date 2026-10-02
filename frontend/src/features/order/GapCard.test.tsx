import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GapCard } from '@/features/order/GapCard';
import type { LabelGap } from '@/features/pages/gaps';

/** The card of a gap in the printed numbers: which numbers it says are missing, and the button that adds them. */

const TILE_WIDTH_PX = 160;

const ARABIC_GAP: LabelGap = {
  afterPageId: 'a',
  beforePageId: 'b',
  style: 'arabic',
  jumpFrom: 46,
  jumpTo: 49,
  firstMissing: 47,
  lastMissing: 48,
};

describe('GapCard', () => {
  let container: HTMLDivElement;
  let root: Root;

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

  function render(gap: LabelGap, busy: boolean, onAdd: () => void): void {
    act(() => root.render(<GapCard gap={gap} busy={busy} width={TILE_WIDTH_PX} onAdd={onAdd} />));
  }

  it('keeps the width of a page tile, even in a cell as wide as a spread', () => {
    render(ARABIC_GAP, false, () => undefined);
    const card = container.querySelector<HTMLElement>('[data-testid="gap-card"]');
    expect(card?.style.maxWidth).toBe(`${TILE_WIDTH_PX}px`);
  });

  it('names the missing pages and the jump in the numbers', () => {
    render(ARABIC_GAP, false, () => undefined);
    expect(container.textContent).toContain('p. 47–48 missing?');
    expect(container.textContent).toContain('The numbers jump from 46 to 49');
  });

  it('names one missing page without a range', () => {
    render({ ...ARABIC_GAP, jumpTo: 48, lastMissing: 47 }, false, () => undefined);
    expect(container.textContent).toContain('p. 47 missing?');
    expect(container.textContent).not.toContain('47–47');
  });

  it('writes Roman numbers in Roman', () => {
    const roman: LabelGap = {
      ...ARABIC_GAP,
      style: 'roman-lower',
      jumpFrom: 2,
      jumpTo: 5,
      firstMissing: 3,
      lastMissing: 4,
    };
    render(roman, false, () => undefined);
    expect(container.textContent).toContain('p. iii–iv missing?');
    expect(container.textContent).toContain('The numbers jump from ii to v');
  });

  it('adds the missing pages when the button is pressed, and not while they are being added', () => {
    const onAdd = vi.fn();
    render(ARABIC_GAP, false, onAdd);
    const button = container.querySelector('button');
    act(() => button?.click());
    expect(onAdd).toHaveBeenCalledTimes(1);

    render(ARABIC_GAP, true, onAdd);
    expect(container.querySelector('button')?.disabled).toBe(true);
    expect(container.querySelector('button')?.textContent).toBe('Adding…');
  });
});
