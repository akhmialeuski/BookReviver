import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageStepChangeSchema } from '@/api';
import { deskew } from '@/features/processing/fixtures';
import { PageStepHistory } from '@/features/processing/PageStepHistory';
import type { StepDraft } from '@/features/processing/recipe';
import { ProblemError } from '@/shared/http/problem';

/**
 * The history of one step on the open page: the changes newest first with what each did, the undo of the newest change
 * by the button and by Ctrl+Z, the undo back to a row, and the rows an undo took back.
 */

const sdk = vi.hoisted(() => ({
  history: vi.fn(),
  undo: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
}));

const SAVED: StepDraft = {
  id: 'step-0',
  stepId: 'step-of-deskew',
  processorKey: 'geometry.deskew',
  params: { max_angle: 5, min_confidence: 0.3 },
  enabled: true,
  appliesTo: 'all',
};

function change(overrides: Partial<PageStepChangeSchema>): PageStepChangeSchema {
  return {
    id: 'c',
    page_id: 'page',
    stage: 'geometry',
    step_id: SAVED.stepId ?? '',
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

/** The history of two changes, the newest first: an edit by hand over a setting of the page. */
const TWO_CHANGES = [
  change({
    id: 'hand',
    layer: 'hand',
    before: null,
    after: { kind: 'rotation', geometry: { degrees: 1.5 }, mask_key: null, edit_hash: 'abc' },
    sequence: 2,
  }),
  change({ id: 'setting', before: null, after: { max_angle: 3 }, sequence: 1 }),
];

function pageOf(items: readonly PageStepChangeSchema[]) {
  return { data: { items, total: items.length, page: 1, size: 100, pages: 1 } };
}

describe('PageStepHistory', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(step = SAVED): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <PageStepHistory
            processing={{ projectId: 'project', stage: 'geometry' }}
            step={step}
            processor={deskew()}
            pageId="page"
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

  const byId = (id: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const rows = (): HTMLElement[] => [
    ...container.querySelectorAll<HTMLElement>('[data-testid="page-history-row"]'),
  ];

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.history.mockReset();
    sdk.undo.mockReset();
    sdk.history.mockResolvedValue(pageOf(TWO_CHANGES));
    sdk.undo.mockResolvedValue({ data: { changes: [] } });
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
  });

  it('reads the history of the step on the page, and lists each change with its layer, source and values', async () => {
    await render();

    expect(sdk.history.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: SAVED.stepId },
    });
    const [hand, setting] = rows();
    expect(hand?.textContent).toContain('Set by hand · You');
    expect(hand?.textContent).toContain('nothing → {"degrees":1.5}');
    expect(setting?.textContent).toContain('Settings of the page · You');
    expect(setting?.textContent).toContain('nothing → Largest slant: 3');
  });

  it('says nothing has changed for a step with no history', async () => {
    sdk.history.mockResolvedValue(pageOf([]));
    await render();

    expect(byId('page-history-none')?.textContent).toContain('Nothing has changed');
    expect(byId('page-history-undo')?.hasAttribute('disabled')).toBe(true);
  });

  it('marks the rows an undo took back and offers no undo for them or for an undo', async () => {
    sdk.history.mockResolvedValue(
      pageOf([
        change({ id: 'undo', source: 'undo', undoes: 'hand', sequence: 3 }),
        { ...TWO_CHANGES[0], undone: true } as PageStepChangeSchema,
        TWO_CHANGES[1] as PageStepChangeSchema,
      ]),
    );
    await render();

    const [undo, hand, setting] = rows();
    expect(hand?.dataset.undone).toBe('true');
    expect(hand?.textContent).toContain('Undone');
    expect(undo?.textContent).toContain('An undo');
    expect(
      [undo, hand, setting].map(
        (row) => row?.querySelector('[data-testid="page-history-undo-back"]') !== null,
      ),
    ).toEqual([false, false, true]);
  });

  it('marks the change of a batch', async () => {
    sdk.history.mockResolvedValue(
      pageOf([change({ id: 'carried', source: 'carry-over', batch_id: 'batch' })]),
    );
    await render();

    expect(rows()[0]?.textContent).toContain('A carry-over');
    expect(rows()[0]?.textContent).toContain('Part of a batch');
  });

  it('takes back the newest change with the button, naming no change', async () => {
    await render();

    await act(async () => byId('page-history-undo')?.click());

    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: SAVED.stepId },
      body: { change_id: null },
    });
  });

  it('takes back a row and the ones after it by the identifier of the row', async () => {
    await render();

    const [, setting] = rows();
    await act(async () =>
      setting?.querySelector<HTMLElement>('[data-testid="page-history-undo-back"]')?.click(),
    );

    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({ body: { change_id: 'setting' } });
  });

  it('takes back the newest change with Ctrl+Z', async () => {
    await render();

    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }),
      );
    });

    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({ body: { change_id: null } });
  });

  it('does nothing for Ctrl+Z when no change stands', async () => {
    sdk.history.mockResolvedValue(pageOf([]));
    await render();

    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }),
      );
    });

    expect(sdk.undo).not.toHaveBeenCalled();
  });

  it('shows the answer of the server when an undo is refused', async () => {
    sdk.undo.mockRejectedValue(new ProblemError('The settings changed since.', 409, null, []));
    await render();

    await act(async () => byId('page-history-undo')?.click());
    await settle();

    expect(container.textContent).toContain('The settings changed since.');
  });

  it('reads the history again after an undo', async () => {
    await render();
    sdk.history.mockClear();

    await act(async () => byId('page-history-undo')?.click());
    await settle();

    expect(sdk.history).toHaveBeenCalled();
  });

  it('offers no history for a step that is not saved yet', async () => {
    await render({ ...SAVED, stepId: null });

    expect(byId('page-history')).toBeNull();
    expect(sdk.history).not.toHaveBeenCalled();
  });
});
