import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  GRID_STORAGE_PREFIX,
  gridStorageKey,
  isGridKey,
  readGrid,
  useGrid,
  useGridKey,
  writeGrid,
} from '@/features/workspace/grid';
import type { WorkspaceStorage } from '@/features/workspace/storage';

/** The grid over the page of a Geometry step: the choice kept for a book, the fallback, and the key G. */

function memory(initial: Record<string, string> = {}): WorkspaceStorage & {
  data: Map<string, string>;
} {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

function press(
  key: string,
  init: KeyboardEventInit = {},
  target: EventTarget = document.body,
): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

describe('readGrid and writeGrid', () => {
  it('keep the choice of one book apart from the choice of another', () => {
    const storage = memory();

    writeGrid('book-a', true, storage);
    writeGrid('book-b', false, storage);

    expect(readGrid('book-a', storage)).toBe(true);
    expect(readGrid('book-b', storage)).toBe(false);
    expect(storage.data.get(gridStorageKey('book-a'))).toBe('on');
    expect(gridStorageKey('book-a').startsWith(GRID_STORAGE_PREFIX)).toBe(true);
  });

  it('read nothing for a book that has no choice and for text that is no choice', () => {
    expect(readGrid('book-a', memory())).toBeNull();
    expect(readGrid('book-a', memory({ [gridStorageKey('book-a')]: 'maybe' }))).toBeNull();
  });
});

describe('isGridKey', () => {
  it('takes G in either case and nothing with a modifier', () => {
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'g' }), false)).toBe(true);
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'G', shiftKey: true }), false)).toBe(true);
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'g', ctrlKey: true }), false)).toBe(false);
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'g', metaKey: true }), false)).toBe(false);
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'g', altKey: true }), false)).toBe(false);
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'h' }), false)).toBe(false);
  });

  it('leaves the key to an open dialog', () => {
    expect(isGridKey(new KeyboardEvent('keydown', { key: 'g' }), true)).toBe(false);
  });

  it('leaves the key to a field being typed in', () => {
    const input = document.createElement('input');
    document.body.append(input);
    let taken: boolean | null = null;
    // The target of an event is known only while it is dispatched, so the check is made inside a listener
    input.addEventListener('keydown', (event) => {
      taken = isGridKey(event, false);
    });
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }));
    input.remove();

    expect(taken).toBe(false);
  });
});

describe('useGrid and useGridKey', () => {
  let container: HTMLDivElement;
  let root: Root;

  function Harness({
    projectId,
    fallback,
    keyed = true,
  }: {
    projectId: string;
    fallback: boolean;
    keyed?: boolean;
  }): React.JSX.Element {
    const [on, toggle] = useGrid(projectId, fallback);
    useGridKey(toggle, keyed);
    return (
      <button type="button" data-testid="grid" data-on={on} onClick={toggle}>
        grid
      </button>
    );
  }

  const state = (): string | null =>
    container.querySelector('[data-testid="grid"]')?.getAttribute('data-on') ?? null;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    localStorage.clear();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('follows the fallback until the reader chooses, and remembers the choice for the book', () => {
    act(() => root.render(<Harness projectId="book-a" fallback />));
    expect(state()).toBe('true');
    expect(localStorage.getItem(gridStorageKey('book-a'))).toBeNull();

    act(() => container.querySelector('button')?.click());
    expect(state()).toBe('false');
    expect(localStorage.getItem(gridStorageKey('book-a'))).toBe('off');

    // Once chosen, the step no longer decides
    act(() => root.render(<Harness projectId="book-a" fallback />));
    expect(state()).toBe('false');
  });

  it('starts from what the book kept, and does not carry a choice over to another book', () => {
    localStorage.setItem(gridStorageKey('book-a'), 'on');

    act(() => root.render(<Harness projectId="book-a" fallback={false} />));
    expect(state()).toBe('true');

    act(() => root.render(<Harness projectId="book-b" fallback={false} />));
    expect(state()).toBe('false');
  });

  it('switches with the key G, and not while a field is being typed in or the screen has no grid', () => {
    act(() => root.render(<Harness projectId="book-a" fallback={false} />));

    const event = press('g');
    expect(event.defaultPrevented).toBe(true);
    expect(state()).toBe('true');
    press('G', { shiftKey: true });
    expect(state()).toBe('false');

    const field = document.createElement('input');
    container.append(field);
    press('g', {}, field);
    expect(state()).toBe('false');

    act(() => root.render(<Harness projectId="book-a" fallback={false} keyed={false} />));
    press('g');
    expect(state()).toBe('false');
  });
});
