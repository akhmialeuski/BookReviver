import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EditorControls } from '@/features/editors/EditorControls';
import type { EditorSession } from '@/features/editors/session';
import { SourceKind } from '@/features/processing/compare';

/** The controls of the page editor in the panel: the editor's own part, "Set by hand" and "Auto". */

function session(overrides: Partial<EditorSession> = {}): EditorSession {
  return {
    picture: { kind: SourceKind.Iiif, url: '/info.json' },
    alwaysOn: false,
    focused: false,
    figure: 'default',
    active: false,
    steps: [],
    choose: vi.fn(),
    hasEdit: false,
    busy: false,
    error: null,
    open: vi.fn(),
    close: vi.fn(),
    auto: vi.fn(),
    reach: null,
    renderCanvas: () => null,
    renderPanel: () => <span data-testid="own-part">own</span>,
    ...overrides,
  };
}

describe('EditorControls', () => {
  let container: HTMLDivElement;
  let root: Root;

  function render(value: EditorSession): void {
    act(() => root.render(<EditorControls session={value} />));
  }

  const button = (name: string): HTMLButtonElement | undefined =>
    [...container.querySelectorAll('button')].find((candidate) =>
      candidate.textContent?.includes(name),
    );

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

  it('draws the part the editor puts in the panel next to the two buttons', () => {
    render(session());

    expect(container.querySelector('[data-testid="own-part"]')).not.toBeNull();
    expect(button('Set by hand')).toBeDefined();
    expect(button('Auto')).toBeDefined();
  });

  it('opens the editor with Set by hand and shuts it with the same button', () => {
    const opening = session();
    render(opening);
    expect(button('Set by hand')?.getAttribute('aria-pressed')).toBe('false');
    act(() => button('Set by hand')?.click());
    expect(opening.open).toHaveBeenCalledTimes(1);

    const shutting = session({ active: true });
    render(shutting);
    expect(button('Set by hand')?.getAttribute('aria-pressed')).toBe('true');
    act(() => button('Set by hand')?.click());
    expect(shutting.close).toHaveBeenCalledTimes(1);
  });

  it('has no Set by hand for an editor that is open whenever the stage is', () => {
    render(session({ alwaysOn: true, active: true }));

    expect(button('Set by hand')).toBeUndefined();
    expect(button('Auto')).toBeDefined();
  });

  it('offers Auto only for a page that has an edit, and not while a change is being made', () => {
    render(session({ hasEdit: false }));
    expect(button('Auto')?.disabled).toBe(true);

    render(session({ hasEdit: true, busy: true }));
    expect(button('Auto')?.disabled).toBe(true);
    expect(container.querySelector('[data-testid="editor-busy"]')).not.toBeNull();

    const ready = session({ hasEdit: true });
    render(ready);
    expect(button('Auto')?.disabled).toBe(false);
    act(() => button('Auto')?.click());
    expect(ready.auto).toHaveBeenCalledTimes(1);
  });

  it('lists the steps of a stage that have an editor and shows the one that is chosen', () => {
    const picking = session({
      steps: [
        { key: 'a', title: 'Sheet corners', manual: true, detail: null, chosen: false },
        { key: 'b', title: 'Angle', manual: false, detail: '0.3°', chosen: true },
        { key: 'c', title: 'Content frame', manual: false, detail: null, chosen: false },
      ],
    });
    render(picking);

    const items = [...container.querySelectorAll('[data-testid="editor-step"]')];
    expect(items.map((item) => item.textContent)).toEqual([
      'Sheet cornersby hand',
      'Angle0.3° · auto',
      'Content frameauto',
    ]);
    expect(items.map((item) => item.getAttribute('data-manual'))).toEqual([
      'true',
      'false',
      'false',
    ]);
    expect(items.map((item) => item.getAttribute('aria-pressed'))).toEqual([
      'false',
      'true',
      'false',
    ]);
    act(() => button('Content frame')?.click());
    expect(picking.choose).toHaveBeenCalledWith('c');
  });

  it('draws no list of steps for the editor of a step that is open in the workspace, which names the step itself', () => {
    render(
      session({
        focused: true,
        alwaysOn: true,
        steps: [
          { key: 'a', title: 'Sheet corners', manual: false, detail: null, chosen: false },
          { key: 'b', title: 'Angle', manual: false, detail: null, chosen: true },
        ],
      }),
    );

    expect(container.querySelector('[data-testid="editor-steps"]')).toBeNull();
    expect(button('Set by hand')).toBeUndefined();
  });

  it('draws no list for a stage with one editor', () => {
    render(
      session({
        steps: [{ key: 'a', title: 'Split line', manual: false, detail: null, chosen: true }],
      }),
    );

    expect(container.querySelector('[data-testid="editor-steps"]')).toBeNull();
  });

  it('says why a change could not be saved', () => {
    render(session({ error: 'The shape does not fit.' }));

    expect(container.textContent).toContain('The shape does not fit.');
  });
});
