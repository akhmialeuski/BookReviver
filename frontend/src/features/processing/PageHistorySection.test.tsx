import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageStepChangeSchema } from '@/api';
import { deskew } from '@/features/processing/fixtures';
import { HISTORY_OPEN_KEY } from '@/features/processing/historyOpen';
import { PageHistorySection } from '@/features/processing/PageHistorySection';
import { ProblemError } from '@/shared/http/problem';

/**
 * The history of one step on the open page: collapsed with the count, disabled when there is nothing to show, three
 * rows at a time when open, the undo back to a row with its question, the clear with its question, Ctrl+Z, and the
 * choice of open or collapsed that is remembered.
 */

const sdk = vi.hoisted(() => ({
  history: vi.fn(),
  undo: vi.fn(),
  clear: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
  clearHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdDelete: sdk.clear,
}));

const STEP_ID = 'step-of-deskew';
const PAGE_SIZE = 3;

function change(overrides: Partial<PageStepChangeSchema>): PageStepChangeSchema {
  return {
    id: 'c',
    page_id: 'page',
    stage: 'geometry',
    step_id: STEP_ID,
    layer: 'settings',
    before: null,
    after: { max_angle: 3 },
    source: 'user',
    batch_id: null,
    undoes: null,
    undone: false,
    created_at: '2026-10-01T10:30:00Z',
    sequence: 1,
    ...overrides,
  };
}

/** The history of `count` settings changes of the page, the newest first, named `c<count>` down to `c1`. */
function settingsChanges(count: number): PageStepChangeSchema[] {
  return Array.from({ length: count }, (_, index) =>
    change({
      id: `c${count - index}`,
      after: { max_angle: count - index },
      sequence: count - index,
    }),
  );
}

/** Answer a request of the list as the server does: the window of the page asked for, and the total of all. */
function serve(all: readonly PageStepChangeSchema[]): void {
  sdk.history.mockImplementation(async (options: { query?: { page?: number; size?: number } }) => {
    const page = options.query?.page ?? 1;
    const size = options.query?.size ?? PAGE_SIZE;
    return {
      data: {
        items: all.slice((page - 1) * size, page * size),
        total: all.length,
        page,
        size,
        pages: Math.ceil(all.length / size),
      },
    };
  });
}

describe('PageHistorySection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(stepId: string | null = STEP_ID): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <PageHistorySection
            projectId="project"
            stage="geometry"
            stepId={stepId}
            pageId="page"
            processor={deskew()}
          />
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const byId = (id: string, within: ParentNode = document): HTMLElement | null =>
    within.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const click = (id: string): Promise<void> =>
    act(async () => {
      byId(id)?.click();
    });
  const rows = (): HTMLElement[] => [
    ...container.querySelectorAll<HTMLElement>('[data-testid="page-history-row"]'),
  ];
  const undoHere = (row: HTMLElement | undefined): HTMLElement | null =>
    row === undefined ? null : byId('page-history-undo-here', row);
  const pressUndo = async (): Promise<void> => {
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }),
      );
    });
  };

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    localStorage.clear();
    sdk.history.mockReset();
    sdk.undo.mockReset();
    sdk.clear.mockReset();
    serve(settingsChanges(2));
    sdk.undo.mockResolvedValue({ data: { changes: [] } });
    sdk.clear.mockResolvedValue({ data: { deleted: 2 } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('is grey and cannot be opened for a step that is not saved, and reads nothing', async () => {
    await render(null);

    expect(byId('page-history')?.getAttribute('aria-disabled')).toBe('true');
    expect(byId('page-history-toggle')?.hasAttribute('disabled')).toBe(true);
    expect(byId('page-history-reason')?.textContent).toContain('not saved yet');
    expect(byId('page-history-count')).toBeNull();
    expect(sdk.history).not.toHaveBeenCalled();
  });

  it('is grey and cannot be opened for a step with no change on the page', async () => {
    serve([]);
    await render();

    expect(byId('page-history')?.getAttribute('aria-disabled')).toBe('true');
    expect(byId('page-history-toggle')?.hasAttribute('disabled')).toBe(true);
    expect(byId('page-history-reason')?.textContent).toContain('Nothing has changed');
    expect(byId('page-history-list')).toBeNull();
  });

  it('stays collapsed when the reader chose to open it for a step with no change', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    serve([]);
    await render();

    expect(byId('page-history-list')).toBeNull();
    expect(byId('page-history-clear')).toBeNull();
  });

  it('is collapsed by default with the title and the number of changes, and no undo on the header', async () => {
    serve(settingsChanges(7));
    await render();

    expect(byId('page-history')?.getAttribute('aria-disabled')).toBe('false');
    expect(byId('page-history')?.textContent).toContain('History of this page');
    expect(byId('page-history-count')?.textContent).toBe('7 changes');
    expect(byId('page-history-list')).toBeNull();
    expect(byId('page-history-undo-here')).toBeNull();
    expect(container.textContent).not.toMatch(/\bUndo\b/);
    expect(sdk.history.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: STEP_ID },
      query: { size: PAGE_SIZE },
    });
  });

  it('lists the newest three when opened, and loads three more at a time until nothing is left', async () => {
    serve(settingsChanges(7));
    await render();

    await click('page-history-toggle');
    expect(rows().map((row) => row.textContent)).toEqual([
      expect.stringContaining('Largest slant: 7'),
      expect.stringContaining('Largest slant: 6'),
      expect.stringContaining('Largest slant: 5'),
    ]);
    expect(byId('page-history-more')?.textContent).toBe('Show 3 more');

    await click('page-history-more');
    await settle();
    expect(rows()).toHaveLength(6);
    expect(byId('page-history-more')?.textContent).toBe('Show 1 more');

    await click('page-history-more');
    await settle();
    expect(rows()).toHaveLength(7);
    expect(byId('page-history-more')).toBeNull();
  });

  it('lists each change with its layer, source and values', async () => {
    serve([
      change({
        id: 'hand',
        layer: 'hand',
        after: { kind: 'rotation', geometry: { degrees: 1.5 }, mask_key: null, edit_hash: 'abc' },
        sequence: 2,
      }),
      change({ id: 'setting', sequence: 1 }),
    ]);
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    const [hand, setting] = rows();
    expect(hand?.textContent).toContain('Set by hand · You');
    expect(hand?.textContent).toContain('nothing → {"degrees":1.5}');
    expect(setting?.textContent).toContain('Settings of the page · You');
    expect(setting?.textContent).toContain('nothing → Largest slant: 3');
  });

  it('marks the rows an undo took back and offers no undo for them or for an undo', async () => {
    serve([
      change({ id: 'undo', source: 'undo', undoes: 'hand', sequence: 3 }),
      change({ id: 'hand', layer: 'hand', undone: true, sequence: 2 }),
      change({ id: 'setting', sequence: 1 }),
    ]);
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    const [undo, hand, setting] = rows();
    expect(hand?.dataset.undone).toBe('true');
    expect(hand?.textContent).toContain('Undone');
    expect(undo?.textContent).toContain('An undo');
    expect([undo, hand, setting].map((row) => undoHere(row) !== null)).toEqual([
      false,
      false,
      true,
    ]);
  });

  it('marks the change of a batch', async () => {
    serve([change({ id: 'carried', source: 'carry-over', batch_id: 'batch' })]);
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    expect(rows()[0]?.textContent).toContain('A carry-over');
    expect(rows()[0]?.textContent).toContain('Part of a batch');
  });

  it('takes back the newest change at once with its own button, naming that change', async () => {
    serve(settingsChanges(3));
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    await act(async () => undoHere(rows()[0])?.click());

    expect(byId('page-history-dialog')).toBeNull();
    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: STEP_ID },
      body: { change_id: 'c3' },
    });
  });

  it('asks first before it takes back an older change with the ones after it', async () => {
    serve(settingsChanges(3));
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    await act(async () => undoHere(rows()[2])?.click());

    expect(sdk.undo).not.toHaveBeenCalled();
    const dialog = byId('page-history-dialog');
    expect(dialog?.textContent).toContain('Undo 3 changes?');
    expect(dialog?.textContent).toContain('Cancel');

    await click('page-history-confirm');

    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({ body: { change_id: 'c1' } });
    expect(byId('page-history-dialog')).toBeNull();
  });

  it('counts only the changes that stand when it asks', async () => {
    serve([
      change({ id: 'undo', source: 'undo', undoes: 'gone', sequence: 4 }),
      change({ id: 'gone', undone: true, sequence: 3 }),
      change({ id: 'newer', sequence: 2 }),
      change({ id: 'older', sequence: 1 }),
    ]);
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();
    await click('page-history-more');
    await settle();

    await act(async () => undoHere(rows()[3])?.click());

    expect(byId('page-history-dialog')?.textContent).toContain('Undo 2 changes?');
  });

  it('takes nothing back when the question is cancelled', async () => {
    serve(settingsChanges(3));
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    await act(async () => undoHere(rows()[1])?.click());
    await act(async () => {
      [...document.querySelectorAll<HTMLElement>('[data-testid="page-history-dialog"] button')]
        .find((button) => button.textContent === 'Cancel')
        ?.click();
    });

    expect(sdk.undo).not.toHaveBeenCalled();
    expect(byId('page-history-dialog')).toBeNull();
  });

  it('asks before it clears the history, and clears it when that is confirmed', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    await click('page-history-clear');

    expect(sdk.clear).not.toHaveBeenCalled();
    expect(byId('page-history-dialog')?.textContent).toContain('cannot be undone');
    expect(byId('page-history-confirm')?.textContent).toBe('Clear and reset');

    await click('page-history-confirm');

    expect(sdk.clear).toHaveBeenCalledTimes(1);
    expect(sdk.clear.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: STEP_ID },
    });
    expect(byId('page-history-dialog')).toBeNull();
  });

  it('clears nothing when the question is cancelled', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    await click('page-history-clear');
    await act(async () => {
      [...document.querySelectorAll<HTMLElement>('[data-testid="page-history-dialog"] button')]
        .find((button) => button.textContent === 'Cancel')
        ?.click();
    });

    expect(sdk.clear).not.toHaveBeenCalled();
  });

  it('reads the history again after a clear', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();
    await click('page-history-clear');
    sdk.history.mockClear();

    await click('page-history-confirm');
    await settle();

    expect(sdk.history).toHaveBeenCalled();
  });

  it('remembers that it was opened, and that it was closed again, for the next one', async () => {
    await render();
    await click('page-history-toggle');
    expect(rows()).toHaveLength(2);

    act(() => root.unmount());
    root = createRoot(container);
    await render();
    expect(rows()).toHaveLength(2);

    await click('page-history-toggle');
    act(() => root.unmount());
    root = createRoot(container);
    await render();
    expect(byId('page-history-list')).toBeNull();
  });

  it('stays collapsed when the storage throws', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    });
    await render();

    expect(byId('page-history-list')).toBeNull();
    await click('page-history-toggle');
    expect(rows()).toHaveLength(2);
  });

  it('takes back the newest change with Ctrl+Z, even while the history is collapsed', async () => {
    await render();

    await pressUndo();

    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({ body: { change_id: null } });
  });

  it('takes back two changes when Ctrl+Z is pressed twice while the first undo is still settling', async () => {
    let finishFirst: (value: { data: { changes: never[] } }) => void = () => {};
    sdk.undo.mockReturnValueOnce(
      new Promise((resolve) => {
        finishFirst = resolve;
      }),
    );
    await render();

    await pressUndo();
    await pressUndo();
    await act(async () => finishFirst({ data: { changes: [] } }));
    await settle();
    await settle();

    expect(sdk.undo).toHaveBeenCalledTimes(2);
  });

  it('does nothing for Ctrl+Z when the step has no change, or is not saved', async () => {
    serve([]);
    await render();
    await pressUndo();
    await render(null);
    await pressUndo();

    expect(sdk.undo).not.toHaveBeenCalled();
  });

  it('takes back with Ctrl+Z when the loaded rows are undos and an older change stands on the next page', async () => {
    serve([
      change({ id: 'u3', source: 'undo', undoes: 'c3', sequence: 6 }),
      change({ id: 'u2', source: 'undo', undoes: 'c2', sequence: 5 }),
      change({ id: 'u1', source: 'undo', undoes: 'c1', sequence: 4 }),
      change({ id: 'c3', undone: true, sequence: 3 }),
      change({ id: 'c2', undone: true, sequence: 2 }),
      change({ id: 'c1', undone: true, sequence: 1 }),
      change({ id: 'c0', sequence: 0 }),
    ]);
    await render();

    await pressUndo();

    expect(sdk.undo).toHaveBeenCalledTimes(1);
  });

  it('shows the answer of the server when an undo is refused', async () => {
    sdk.undo.mockRejectedValue(new ProblemError('The settings changed since.', 409, null, []));
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();

    await act(async () => undoHere(rows()[0])?.click());
    await settle();

    expect(container.textContent).toContain('The settings changed since.');
  });

  it('reads the history again after an undo', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render();
    sdk.history.mockClear();

    await act(async () => undoHere(rows()[0])?.click());
    await settle();

    expect(sdk.history).toHaveBeenCalled();
  });

  it('says the history could not be read when the server refuses', async () => {
    sdk.history.mockRejectedValue(new ProblemError('Broken.', 500, null, []));
    await render();

    expect(container.textContent).toContain('could not be read');
  });
});
