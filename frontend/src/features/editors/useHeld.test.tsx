import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useHeld } from '@/features/editors/useHeld';

/** The value a screen shows while the value that follows is not to be trusted: it is the last one that was. */

const FIRST = ['first'];
const SECOND = ['second'];

describe('useHeld', () => {
  let container: HTMLDivElement;
  let root: Root;
  let shown: string[] | null;

  function Probe({ id, value, holding }: { id: string; value: string[]; holding: boolean }): null {
    shown = useHeld(id, value, holding);
    return null;
  }

  async function render(id: string, value: string[], holding: boolean): Promise<void> {
    await act(async () => {
      root.render(<Probe id={id} value={value} holding={holding} />);
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    shown = null;
    container = document.createElement('div');
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  });

  it('gives the value of now while nothing is held', async () => {
    await render('page', FIRST, false);
    await render('page', SECOND, false);

    expect(shown).toBe(SECOND);
  });

  it('keeps the last value while the next one is held, and goes to the one that stays', async () => {
    await render('page', FIRST, false);
    await render('page', SECOND, true);
    expect(shown).toBe(FIRST);

    await render('page', SECOND, false);
    expect(shown).toBe(SECOND);
  });

  it('never gives the value of another key, so a page turn is not held', async () => {
    await render('page', FIRST, false);
    await render('other', SECOND, true);

    expect(shown).toBe(SECOND);
  });
});
