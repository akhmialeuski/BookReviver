import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NARROW_QUERY, useIsNarrow, useMediaQuery } from '@/shared/hooks/useMediaQuery';

/**
 * The media query hook follows a faked `matchMedia`: the match at the first render, the match after a change event,
 * and the end of the subscription when the component goes away.
 */

class FakeMediaQueryList extends EventTarget {
  matches: boolean;

  constructor(matches: boolean) {
    super();
    this.matches = matches;
  }

  set(matches: boolean): void {
    this.matches = matches;
    this.dispatchEvent(new Event('change'));
  }
}

function Probe({ query }: { query: string }): React.JSX.Element {
  return <span data-testid="probe">{String(useMediaQuery(query))}</span>;
}

function NarrowProbe(): React.JSX.Element {
  return <span data-testid="narrow">{String(useIsNarrow())}</span>;
}

describe('useMediaQuery', () => {
  let container: HTMLDivElement;
  let root: Root;
  let list: FakeMediaQueryList;
  const queries: string[] = [];

  const text = (id: string): string =>
    container.querySelector(`[data-testid="${id}"]`)?.textContent ?? '';

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    queries.length = 0;
    list = new FakeMediaQueryList(false);
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => {
        queries.push(query);
        return list;
      }),
    );
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('reads the match of the query at the first render', () => {
    list.matches = true;
    act(() => root.render(<Probe query="(width < 10px)" />));

    expect(text('probe')).toBe('true');
    expect(queries).toContain('(width < 10px)');
  });

  it('follows the window as the match changes', () => {
    act(() => root.render(<Probe query="(width < 10px)" />));
    expect(text('probe')).toBe('false');

    act(() => list.set(true));
    expect(text('probe')).toBe('true');

    act(() => list.set(false));
    expect(text('probe')).toBe('false');
  });

  it('stops listening once the component is gone', () => {
    const remove = vi.spyOn(list, 'removeEventListener');
    act(() => root.render(<Probe query="(width < 10px)" />));
    act(() => root.render(<span />));

    expect(remove).toHaveBeenCalledWith('change', expect.any(Function));
  });

  it('asks about the lg breakpoint of Tailwind for the narrow layout', () => {
    list.matches = true;
    act(() => root.render(<NarrowProbe />));

    expect(text('narrow')).toBe('true');
    expect(NARROW_QUERY).toBe('(width < 1024px)');
    expect(queries).toContain(NARROW_QUERY);
  });

  it('never matches in a browser without matchMedia', () => {
    vi.stubGlobal('matchMedia', undefined);
    act(() => root.render(<Probe query="(width < 10px)" />));

    expect(text('probe')).toBe('false');
  });
});
