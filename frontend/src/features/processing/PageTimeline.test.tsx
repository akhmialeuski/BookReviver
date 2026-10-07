import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageStepChangeSchema } from '@/api';
import { processing, recipe, step, version } from '@/features/processing/fixtures';
import { PageTimeline } from '@/features/processing/PageTimeline';
import { HISTORY_OPEN_KEY } from '@/features/workspace/historyOpen';
import { StagePanel } from '@/features/workspace/StagePanel';
import { ProblemError } from '@/shared/http/problem';

/**
 * The history of the open page as one list: the changes of a step and the results merged by time, each with its chip,
 * the filters, three rows at a time over both lists, the undo back to a change, the use of a result, the clear that
 * deletes the changes and the results, the stage with no step open, and every reason for being grey. The frame, the
 * remembered choice of open or collapsed, and the notes on a result are tested with `HistoryFrame`, `historyOpen` and
 * `ResultNote`.
 */

const sdk = vi.hoisted(() => ({
  history: vi.fn(),
  undo: vi.fn(),
  clear: vi.fn(),
  versions: vi.fn(),
  choose: vi.fn(),
  remake: vi.fn(),
  mark: vi.fn(),
  jobs: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
  clearHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdDelete: sdk.clear,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
  chooseVersionApiV1ProjectsProjectIdPagesPageIdStagesStagePut: sdk.choose,
  remakeVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdRemakePost: sdk.remake,
  putMarkApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdMarkPut: sdk.mark,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

const STEP_ID = 'id-geometry.deskew';
const STEP = { stepId: STEP_ID, processorKey: 'geometry.deskew' };
const EMPTY_LIST = { data: { items: [], total: 0, page: 1, size: 100, pages: 1 } };

/** A time of the day of the first of October, which orders the changes and the results of a test. */
function at(hour: number, minute = 0): string {
  return `2026-10-01T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00Z`;
}

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
    created_at: at(10, 30),
    sequence: 1,
    ...overrides,
  };
}

/** `count` settings changes of the page, the newest first, named `c<count>` down to `c1` and an hour apart from 10:30. */
function settingsChanges(count: number): PageStepChangeSchema[] {
  return Array.from({ length: count }, (_, index) =>
    change({
      id: `c${count - index}`,
      after: { max_angle: count - index },
      created_at: at(10 + count - index - 1, 30),
      sequence: count - index,
    }),
  );
}

/** Answer a request of the changes as the server does: the window of the page asked for, and the total of all. */
function serve(all: readonly PageStepChangeSchema[], size = 100): void {
  sdk.history.mockImplementation(async (options: { query?: { page?: number } }) => {
    const page = options.query?.page ?? 1;
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

function listed(...items: ReturnType<typeof version>[]): { data: unknown } {
  return { data: { items, total: items.length, page: 1, size: 100, pages: 1 } };
}

const FIRST = version('first', { created_at: at(9), mark: 'bad' });
const OLD = version('old', {
  created_at: at(11),
  input_id: 'first',
  params: { max_angle: 5, min_confidence: 0.3 },
  mark: 'good',
});
const NEW = version('new', {
  created_at: at(12),
  input_id: 'first',
  params: { max_angle: 9, min_confidence: 0.3 },
  data: { angle: 1.4, confidence: 0.91 },
  edit_hash: '0123456789abcdef',
  origin: 'hand',
  mark: 'bad',
});

describe('PageTimeline', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  interface Setup {
    /** The step open, or null for the stage as a whole; the step of the recipe by default. */
    step?: { stepId: string | null; processorKey: string } | null;
    /** The page is none when this is set. */
    noPage?: boolean;
    /** The current version of the stage on the page; the newest result by default. */
    headId?: string;
    state?: ReturnType<typeof processing>;
  }

  async function render(setup: Setup = {}): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <StagePanel
            stage="geometry"
            available
            history={
              <PageTimeline
                processing={setup.state ?? processing()}
                pageId={setup.noPage === true ? undefined : 'page'}
                step={setup.step === undefined ? STEP : setup.step}
                headId={setup.headId ?? 'new'}
              />
            }
          />
        </QueryClientProvider>,
      );
    });
    await settle();
    await settle();
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const byId = (id: string, within: ParentNode = document): HTMLElement | null =>
    within.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const click = async (id: string): Promise<void> => {
    await act(async () => {
      byId(id)?.click();
    });
    await settle();
    await settle();
  };
  const rows = (): HTMLElement[] => [
    ...container.querySelectorAll<HTMLElement>('[data-testid="page-history-list"] > li'),
  ];
  /** The rows by what they are: the id of the change or of the result. */
  const order = (): string[] =>
    rows().map((row) => `${row.dataset.kind}:${row.dataset.change ?? row.dataset.version}`);
  const undoHere = (row: HTMLElement | undefined): HTMLElement | null =>
    row === undefined ? null : byId('page-history-undo-here', row);
  const open = (): void => localStorage.setItem(HISTORY_OPEN_KEY, 'open');
  const pressUndo = async (): Promise<void> => {
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }),
      );
    });
  };
  const cancel = async (): Promise<void> => {
    await act(async () => {
      [...document.querySelectorAll<HTMLElement>('[data-testid="page-history-dialog"] button')]
        .find((button) => button.textContent === 'Cancel')
        ?.click();
    });
  };

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    localStorage.clear();
    for (const mock of Object.values(sdk)) {
      mock.mockReset();
    }
    serve([]);
    sdk.versions.mockResolvedValue(EMPTY_LIST);
    sdk.undo.mockResolvedValue({ data: { changes: [] } });
    sdk.clear.mockResolvedValue({ data: { changes: 2, versions: 2 } });
    sdk.choose.mockResolvedValue({ data: {} });
    sdk.remake.mockResolvedValue({ data: { id: 'job' } });
    sdk.mark.mockResolvedValue({ data: {} });
    sdk.jobs.mockResolvedValue(EMPTY_LIST);
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

  describe('the count', () => {
    it('counts the changes and the results of the step together', async () => {
      serve(settingsChanges(2));
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      await render();

      expect(byId('page-history-count')?.textContent).toBe('4 events');
    });
  });

  describe('the list', () => {
    it('merges the changes and the results by time, newest first, each row with the chip of its kind', async () => {
      serve([
        change({ id: 'c2', created_at: at(11, 30), sequence: 2 }),
        change({ id: 'c1', created_at: at(10, 30), sequence: 1 }),
      ]);
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      open();
      await render();

      expect(order()).toEqual(['result:new', 'change:c2', 'result:old']);
      expect(rows()[0]?.textContent).toContain('Result');
      expect(rows()[1]?.textContent).toContain('Change');
      expect(rows()[2]?.textContent).toContain('Result');
    });

    it('shows three rows, and adds three at a time over both lists until nothing is left', async () => {
      serve([
        change({ id: 'c4', created_at: at(13, 30), sequence: 4 }),
        change({ id: 'c3', created_at: at(12, 30), sequence: 3 }),
        change({ id: 'c2', created_at: at(11, 30), sequence: 2 }),
        change({ id: 'c1', created_at: at(10, 30), sequence: 1 }),
      ]);
      sdk.versions.mockResolvedValue(
        listed(
          version('r1', { created_at: at(10) }),
          version('r2', { created_at: at(11) }),
          version('r3', { created_at: at(12) }),
          version('r4', { created_at: at(13) }),
        ),
      );
      open();
      await render();

      expect(order()).toEqual(['change:c4', 'result:r4', 'change:c3']);
      expect(byId('page-history-more')?.textContent).toBe('Show 3 more');

      await click('page-history-more');
      expect(order()).toEqual([
        'change:c4',
        'result:r4',
        'change:c3',
        'result:r3',
        'change:c2',
        'result:r2',
      ]);
      expect(byId('page-history-more')?.textContent).toBe('Show 2 more');

      await click('page-history-more');
      expect(rows()).toHaveLength(8);
      expect(byId('page-history-more')).toBeNull();
    });

    it('reads the next page of changes only when the rows asked for reach beyond the ones loaded', async () => {
      serve(settingsChanges(5), 3);
      open();
      await render();

      expect(order()).toEqual(['change:c5', 'change:c4', 'change:c3']);
      expect(sdk.history).toHaveBeenCalledTimes(1);

      await click('page-history-more');

      expect(sdk.history).toHaveBeenCalledTimes(2);
      expect(order()).toEqual(['change:c5', 'change:c4', 'change:c3', 'change:c2', 'change:c1']);
      expect(byId('page-history-more')).toBeNull();
    });

    it('reads the next page when a result older than the loaded changes could have a change above it', async () => {
      serve(settingsChanges(5), 2);
      sdk.versions.mockResolvedValue(listed(version('mid', { created_at: at(12) })));
      open();
      await render();

      // c5 and c4 are loaded, and the result at 12:00 is older than c4, so the third row waits for the next page
      expect(sdk.history.mock.calls.map(([options]) => options.query.page)).toEqual([1, 2]);
      expect(order()).toEqual(['change:c5', 'change:c4', 'change:c3']);

      await click('page-history-more');

      expect(sdk.history.mock.calls.map(([options]) => options.query.page)).toEqual([1, 2, 3]);
      expect(order()).toEqual([
        'change:c5',
        'change:c4',
        'change:c3',
        'result:mid',
        'change:c2',
        'change:c1',
      ]);
    });

    it('asks for the changes a hundred at a time', async () => {
      serve(settingsChanges(2));
      await render();

      expect(sdk.history.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: STEP_ID },
        query: { size: 100 },
      });
    });

    it('lets the text of every row wrap and keeps the button of a row its size, so nothing scrolls sideways', async () => {
      serve([change({ id: 'c', after: { max_angle: 3 } })]);
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      open();
      await render();

      const [current, earlier, changed] = rows();
      expect(byId('page-history')?.className).toContain('min-w-0');
      expect(byId('page-history-list')?.className).toContain('grid-cols-1');
      expect(earlier?.className).toContain('min-w-0');
      expect(byId('page-history-settings', current)?.className).toMatch(/min-w-0.*break-words/);
      expect(byId('page-history-found', current)?.className).toMatch(/min-w-0.*break-words/);
      expect(byId('page-history-change', changed)?.className).toMatch(/min-w-0.*break-words/);
      expect(undoHere(changed)?.className).toContain('shrink-0');
      expect(byId('page-history-use', earlier)?.className).toContain('shrink-0');
    });

    it('puts "Undo to here" in a column of its own to the right of the text of the change, which does not shrink', async () => {
      serve([change({ id: 'c' })]);
      open();
      await render();

      const [changed] = rows();
      const button = undoHere(changed);
      const text = byId('page-history-change', changed)?.parentElement;
      expect(changed?.className).toMatch(/\bflex\b/);
      expect(text?.parentElement).toBe(changed);
      expect(button?.parentElement).toBe(changed);
      expect(text?.nextElementSibling).toBe(button);
      expect(text?.className).toMatch(/\bmin-w-0\b/);
      expect(text?.className).toMatch(/\bflex-1\b/);
      expect(button?.className).toContain('shrink-0');
    });

    it('wraps a long description of a change within the section, since every box up to the section may shrink', async () => {
      const long = 'x'.repeat(300);
      serve([change({ id: 'c', after: { max_angle: long } })]);
      open();
      await render();

      const text = byId('page-history-change', rows()[0]);
      const frame = byId('page-history');
      const boxes: HTMLElement[] = [];
      for (
        let box = text?.parentElement ?? null;
        box !== null && box !== frame;
        box = box.parentElement
      ) {
        boxes.push(box);
      }
      expect(text?.textContent).toContain(long);
      expect(text?.className).toContain('break-words');
      expect(boxes.length).toBeGreaterThan(0);
      expect(boxes.filter((box) => !/\bmin-w-0\b/.test(box.className))).toEqual([]);
    });
  });

  describe('a change', () => {
    it('says who made it and what it did to which layer, with a hand edit in words', async () => {
      serve([
        change({
          id: 'hand',
          layer: 'hand',
          after: { kind: 'rotation', geometry: { degrees: 1.5 }, mask_key: null, edit_hash: 'abc' },
          created_at: at(11, 30),
          sequence: 2,
        }),
        change({ id: 'setting', sequence: 1 }),
      ]);
      open();
      await render();

      const [hand, setting] = rows();
      expect(hand?.textContent).toContain('Set by hand · You');
      expect(hand?.textContent).toContain('nothing → Angle 1.5°');
      expect(hand?.textContent).not.toContain('{');
      expect(setting?.textContent).toContain('Settings of the page · You');
      expect(setting?.textContent).toContain('nothing → Largest slant: 3');
    });

    it('marks the changes an undo took back and offers no undo for them or for an undo', async () => {
      serve([
        change({ id: 'undo', source: 'undo', undoes: 'hand', created_at: at(12, 30), sequence: 3 }),
        change({ id: 'hand', layer: 'hand', undone: true, created_at: at(11, 30), sequence: 2 }),
        change({ id: 'setting', sequence: 1 }),
      ]);
      open();
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
      open();
      await render();

      expect(rows()[0]?.textContent).toContain('A carry-over');
      expect(rows()[0]?.textContent).toContain('Part of a batch');
    });

    it('takes back the newest change at once with its own button, naming that change', async () => {
      serve(settingsChanges(3));
      open();
      await render();

      expect(undoHere(rows()[0])?.querySelector('svg')).not.toBeNull();
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
      open();
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

    it('counts only the changes that stand when it asks, and not the results between them', async () => {
      serve([
        change({ id: 'undo', source: 'undo', undoes: 'gone', created_at: at(13, 30), sequence: 4 }),
        change({ id: 'gone', undone: true, created_at: at(12, 30), sequence: 3 }),
        change({ id: 'newer', created_at: at(11, 30), sequence: 2 }),
        change({ id: 'older', created_at: at(10, 30), sequence: 1 }),
      ]);
      sdk.versions.mockResolvedValue(listed(version('r', { created_at: at(11) })));
      open();
      await render();
      await click('page-history-more');

      const older = container.querySelector<HTMLElement>('[data-change="older"]');
      await act(async () => undoHere(older ?? undefined)?.click());

      expect(byId('page-history-dialog')?.textContent).toContain('Undo 2 changes?');
    });

    it('takes nothing back when the question is cancelled', async () => {
      serve(settingsChanges(3));
      open();
      await render();

      await act(async () => undoHere(rows()[1])?.click());
      await cancel();

      expect(sdk.undo).not.toHaveBeenCalled();
      expect(byId('page-history-dialog')).toBeNull();
    });

    it('shows the answer of the server when an undo is refused', async () => {
      serve(settingsChanges(2));
      sdk.undo.mockRejectedValue(new ProblemError('The settings changed since.', 409, null, []));
      open();
      await render();

      await act(async () => undoHere(rows()[0])?.click());
      await settle();

      expect(container.textContent).toContain('The settings changed since.');
    });

    it('reads the history again after an undo', async () => {
      serve(settingsChanges(2));
      open();
      await render();
      sdk.history.mockClear();

      await act(async () => undoHere(rows()[0])?.click());
      await settle();

      expect(sdk.history).toHaveBeenCalled();
    });
  });

  describe('Ctrl+Z', () => {
    it('takes back the newest change, even while the history is collapsed', async () => {
      serve(settingsChanges(2));
      await render();

      await pressUndo();

      expect(sdk.undo).toHaveBeenCalledTimes(1);
      expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({ body: { change_id: null } });
    });

    it('takes back two changes when it is pressed twice while the first undo is still settling', async () => {
      serve(settingsChanges(2));
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

    it('does nothing when the step has no change, when it is not saved, or when no step is open', async () => {
      await render();
      await pressUndo();
      await render({ step: { stepId: null, processorKey: 'geometry.deskew' } });
      await pressUndo();
      await render({ step: null });
      await pressUndo();

      expect(sdk.undo).not.toHaveBeenCalled();
    });

    it('takes back when the loaded changes are undos and an older change stands on the next page', async () => {
      serve(
        [
          change({ id: 'u3', source: 'undo', undoes: 'c3', created_at: at(16), sequence: 6 }),
          change({ id: 'u2', source: 'undo', undoes: 'c2', created_at: at(15), sequence: 5 }),
          change({ id: 'u1', source: 'undo', undoes: 'c1', created_at: at(14), sequence: 4 }),
          change({ id: 'c3', undone: true, created_at: at(13), sequence: 3 }),
          change({ id: 'c2', undone: true, created_at: at(12), sequence: 2 }),
          change({ id: 'c1', undone: true, created_at: at(11), sequence: 1 }),
          change({ id: 'c0', created_at: at(10), sequence: 0 }),
        ],
        3,
      );
      await render();

      await pressUndo();

      expect(sdk.undo).toHaveBeenCalledTimes(1);
    });
  });

  describe('the filters', () => {
    const CHANGES = [
      change({ id: 'c2', created_at: at(11, 30), sequence: 2 }),
      change({ id: 'c1', created_at: at(10, 30), sequence: 1 }),
    ];

    beforeEach(() => {
      serve(CHANGES);
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      open();
    });

    it('offers All, Changes, Results, Good and Bad, with All pressed', async () => {
      await render();

      const filters = [...container.querySelectorAll('[data-testid^="page-history-filter-"]')];
      expect(filters.map((filter) => filter.textContent)).toEqual([
        'All',
        'Changes',
        'Results',
        'Good',
        'Bad',
      ]);
      expect(byId('page-history-filter-all')?.getAttribute('aria-pressed')).toBe('true');
    });

    it('narrows to the changes, to the results, and to the results marked good or bad', async () => {
      await render();

      await click('page-history-filter-changes');
      expect(order()).toEqual(['change:c2', 'change:c1']);
      expect(byId('page-history-filter-changes')?.getAttribute('aria-pressed')).toBe('true');

      await click('page-history-filter-results');
      expect(order()).toEqual(['result:new', 'result:old']);

      await click('page-history-filter-good');
      expect(order()).toEqual(['result:old']);

      await click('page-history-filter-bad');
      expect(order()).toEqual(['result:new']);

      await click('page-history-filter-all');
      expect(order()).toEqual(['result:new', 'change:c2', 'result:old']);
    });

    it('asks the server nothing more for a filter, and goes back to three rows when one is chosen', async () => {
      await render();
      await click('page-history-more');
      expect(rows()).toHaveLength(4);
      const reads = sdk.versions.mock.calls.length;

      await click('page-history-filter-all');

      expect(rows()).toHaveLength(3);
      expect(sdk.versions).toHaveBeenCalledTimes(reads);
    });

    it('tags the result of the open step that the page stands on, though a later step made the current version', async () => {
      const deskew = version('deskew', { created_at: at(10), input_id: 'perspective' });
      const earlier = version('crop-earlier', { created_at: at(10, 30), input_id: 'deskew' });
      const crop = version('crop', { created_at: at(11), input_id: 'deskew' });
      const normalize = version('normalize', { created_at: at(12), input_id: 'crop' });
      sdk.versions.mockImplementation(async (options: { query?: { step?: string } }) =>
        options.query?.step === undefined
          ? listed(deskew, earlier, crop, normalize)
          : listed(earlier, crop),
      );
      serve([]);

      await render({ headId: 'normalize' });

      expect(order()).toEqual(['result:crop', 'result:crop-earlier']);
      expect(rows().map((row) => row.dataset.current)).toEqual(['true', 'false']);
      expect(rows()[0]?.textContent).toContain('Current');
      expect(rows()[1]?.textContent).not.toContain('Current');
    });

    it('tags nothing while the page stands on no result of the open step, as when a variant of the recipe ran it', async () => {
      const deskew = version('deskew', { created_at: at(10) });
      const crop = version('crop', { created_at: at(11), input_id: 'deskew' });
      const other = version('other', { created_at: at(12) });
      sdk.versions.mockImplementation(async (options: { query?: { step?: string } }) =>
        options.query?.step === undefined ? listed(deskew, crop, other) : listed(crop),
      );
      serve([]);

      await render({ headId: 'other' });

      expect(rows().map((row) => row.dataset.current)).toEqual(['false']);
    });

    it('says which kind is missing when the filter leaves nothing', async () => {
      sdk.versions.mockResolvedValue(listed(version('plain', { created_at: at(12) })));
      await render({ headId: 'plain' });

      await click('page-history-filter-good');
      expect(byId('page-history-empty')?.textContent).toBe(
        'No result of this page is marked good.',
      );
      await click('page-history-filter-bad');
      expect(byId('page-history-empty')?.textContent).toBe('No result of this page is marked bad.');
      await click('page-history-filter-all');
      expect(byId('page-history-empty')).toBeNull();
    });

    it('says the step has no change, and that the stage made no result', async () => {
      serve([]);
      await render();
      await click('page-history-filter-changes');
      expect(byId('page-history-empty')?.textContent).toBe('This step has no change on this page.');

      serve(CHANGES);
      sdk.versions.mockResolvedValue(EMPTY_LIST);
      await render({ step: { stepId: 'other', processorKey: 'geometry.deskew' } });
      await click('page-history-filter-results');
      expect(byId('page-history-empty')?.textContent).toBe('Run the stage to make a result.');
    });
  });

  describe('a result', () => {
    beforeEach(() => {
      sdk.versions.mockResolvedValue(listed(FIRST, OLD, NEW));
      open();
    });

    it('says how it was made and when, with what settings and what it found, and tags the current one', async () => {
      await render({ step: null });

      const [current, earlier] = rows();
      expect(current?.getAttribute('data-current')).toBe('true');
      expect(current?.textContent).toContain('Current');
      expect(byId('page-history-origin', current)?.textContent).toBe('Set by hand');
      expect(byId('page-history-settings', current)?.textContent).toBe(
        'Largest slant 9 · Least confidence 0.3',
      );
      expect(byId('page-history-found', current)?.textContent).toBe(
        'Turned by 1.4° · Confidence 0.91 · sure',
      );
      expect(byId('page-history-use', current)).toBeNull();
      expect(byId('page-history-origin', earlier)?.textContent).toBe('Made by the step');
      expect(byId('page-history-found', earlier)).toBeNull();
      expect(byId('page-history-use', earlier)?.textContent).toBe('Use this');
    });

    it('makes an earlier result the current one', async () => {
      await render({ step: null });

      await act(async () => {
        byId('page-history-use')?.click();
      });

      expect(sdk.choose.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', stage: 'geometry' },
        body: { version_id: 'old' },
      });
    });

    it('makes the picture of a result again when a collection removed it, instead of choosing it', async () => {
      const removed = version('old', {
        created_at: at(11),
        input_id: 'first',
        files_removed: true,
        files_removed_at: '2026-10-02T00:00:00Z',
        images: null,
      });
      sdk.versions.mockResolvedValue(listed(FIRST, removed, NEW));
      await render({ step: null });

      expect(byId('page-history-removed')?.textContent).toBe('Picture removed · made again on use');
      await act(async () => {
        byId('page-history-use')?.click();
      });

      expect(sdk.choose).not.toHaveBeenCalled();
      expect(sdk.remake.mock.calls[0]?.[0]).toMatchObject({ path: { version_id: 'old' } });
    });

    it('shows the answer of the server when a result cannot be chosen', async () => {
      sdk.choose.mockRejectedValue(new ProblemError('This result is gone.', 409, null, []));
      await render({ step: null });

      await act(async () => {
        byId('page-history-use')?.click();
      });
      await settle();

      expect(container.textContent).toContain('This result is gone.');
    });

    it('offers an earlier result as the result of the stage only when its step is the last one', async () => {
      const two = recipe('r1', {
        steps: [step('geometry.deskew'), step('geometry.crop')],
      });
      sdk.versions.mockResolvedValue(listed(OLD, NEW));

      await render({ state: processing({ recipe: two, recipes: [two] }), step: STEP });
      expect(byId('page-history-use')).toBeNull();
      expect(
        container
          .querySelector('[data-testid="page-history-row"][data-kind="result"]')
          ?.getAttribute('data-current'),
      ).toBe('true');

      await render({
        state: processing({ recipe: two, recipes: [two] }),
        step: { stepId: 'id-geometry.crop', processorKey: 'geometry.crop' },
      });
      expect(byId('page-history-use')).not.toBeNull();
    });

    it('is a result of the last enabled step when a step after it is off', async () => {
      const off = recipe('r1', {
        steps: [step('geometry.deskew'), step('geometry.crop', { enabled: false })],
      });
      sdk.versions.mockResolvedValue(listed(OLD, NEW));

      await render({ state: processing({ recipe: off, recipes: [off] }), step: STEP });

      expect(byId('page-history-use')).not.toBeNull();
    });
  });

  describe('the notes on a result', () => {
    beforeEach(() => {
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      open();
    });

    it('offers a mark and a comment on every result, the current one included', async () => {
      await render();

      expect(container.querySelectorAll('[data-testid="result-note"]')).toHaveLength(2);
    });
  });

  describe('clearing the history', () => {
    it('asks before it clears, says what is deleted for good, and clears when that is confirmed', async () => {
      serve(settingsChanges(2));
      open();
      await render();

      await click('page-history-clear');

      expect(sdk.clear).not.toHaveBeenCalled();
      const dialog = byId('page-history-dialog');
      expect(dialog?.textContent).toContain('deletes for good');
      expect(dialog?.textContent).toContain('the results of this step on this page');
      expect(dialog?.textContent).toContain(
        'the results of the later steps of the stage on this page',
      );
      expect(dialog?.textContent).toContain('cannot be undone');
      expect(dialog?.textContent).not.toContain('The results stay');
      expect(byId('page-history-confirm')?.textContent).toBe('Clear and reset');

      await click('page-history-confirm');

      expect(sdk.clear).toHaveBeenCalledTimes(1);
      expect(sdk.clear.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: STEP_ID },
      });
      expect(byId('page-history-dialog')).toBeNull();
    });

    it('clears nothing when the question is cancelled', async () => {
      serve(settingsChanges(2));
      open();
      await render();

      await click('page-history-clear');
      await cancel();

      expect(sdk.clear).not.toHaveBeenCalled();
    });

    it('reads the history, the results and the rows of the stage again, and leaves the list empty and grey', async () => {
      serve(settingsChanges(2));
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      open();
      await render();
      expect(order()).toHaveLength(3);
      await click('page-history-clear');
      sdk.history.mockClear();
      sdk.versions.mockClear();
      serve([]);
      sdk.versions.mockResolvedValue(EMPTY_LIST);

      await click('page-history-confirm');

      expect(sdk.history).toHaveBeenCalled();
      expect(sdk.versions).toHaveBeenCalled();
      expect(order()).toEqual([]);
      expect(byId('page-history')?.getAttribute('aria-disabled')).toBe('true');
      expect(byId('page-history-reason')?.textContent).toContain(
        'Nothing has happened on this page yet.',
      );
      expect(byId('page-history-count')).toBeNull();
    });

    it('marks the results of the page, the rows of the stage and its summary out of date', async () => {
      serve(settingsChanges(2));
      open();
      await render();
      await click('page-history-clear');
      const invalidate = vi.spyOn(client, 'invalidateQueries');

      await click('page-history-confirm');

      const asked = invalidate.mock.calls.map(([filters]) => {
        const head = filters?.queryKey?.[0];
        return typeof head === 'object' && head !== null && '_id' in head ? head._id : null;
      });
      expect(asked).toEqual(
        expect.arrayContaining([
          'listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet',
          'listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet',
          'listStagesApiV1ProjectsProjectIdStagesGet',
        ]),
      );
    });

    it('is the last thing in the list, and is not there while no step is open', async () => {
      serve(settingsChanges(2));
      open();
      await render();

      expect(byId('page-history-clear')?.previousElementSibling).not.toBeNull();
      expect(byId('page-history-clear')?.nextElementSibling).toBeNull();

      await render({ step: null });
      expect(byId('page-history-clear')).toBeNull();
    });
  });

  describe('with no step open', () => {
    beforeEach(() => {
      sdk.versions.mockResolvedValue(listed(FIRST, OLD, NEW));
      open();
    });

    it('lists the results of the stage, leaves out the version a later step read, and reads no change', async () => {
      await render({ step: null });

      expect(order()).toEqual(['result:new', 'result:old']);
      expect(sdk.history).not.toHaveBeenCalled();
      expect(sdk.versions.mock.calls[0]?.[0].query).toEqual({
        stage: 'geometry',
        scale: 'full',
        size: 100,
        page: 1,
      });
      expect(byId('page-history-count')?.textContent).toBe('2 events');
    });

    it('turns the Changes filter off with the hint to open a step', async () => {
      await render({ step: null });

      expect(byId('page-history-filter-changes')?.hasAttribute('disabled')).toBe(true);
      expect(byId('page-history-changes-hint')?.textContent).toBe('Open a step to see its changes');
      expect(byId('page-history-filter-results')?.hasAttribute('disabled')).toBe(false);
    });

    it('has the Changes filter on, and no hint, when a step is open', async () => {
      serve(settingsChanges(1));
      await render();

      expect(byId('page-history-filter-changes')?.hasAttribute('disabled')).toBe(false);
      expect(byId('page-history-changes-hint')).toBeNull();
    });

    it('shows every result when the step is closed while the Changes filter is pressed', async () => {
      serve(settingsChanges(1));
      await render();
      await click('page-history-filter-changes');

      await render({ step: null });

      expect(order()).toEqual(['result:new', 'result:old']);
      expect(byId('page-history-filter-all')?.getAttribute('aria-pressed')).toBe('true');
    });

    it('narrows the results of the stage by the mark from the list it has, with no new request', async () => {
      await render({ step: null });
      await click('page-history-filter-bad');

      expect(order()).toEqual(['result:new']);
      expect(sdk.versions).toHaveBeenCalledTimes(1);
    });

    it('does not list a version a later step read under the mark it carries, since it is no result of the stage', async () => {
      sdk.versions.mockResolvedValue(listed(FIRST, version('last', { input_id: 'first' })));
      await render({ step: null, headId: 'last' });
      await click('page-history-filter-bad');

      expect(order()).toEqual([]);
    });

    it('reads the results of a step by the step', async () => {
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      await render();

      expect(sdk.versions.mock.calls[0]?.[0].query).toMatchObject({
        stage: 'geometry',
        step: STEP_ID,
        scale: 'full',
      });
    });

    it('lists the result of an early step though a later step reads it, since the list holds that step only', async () => {
      sdk.versions.mockResolvedValue(listed(FIRST));
      await render({ headId: 'first' });

      expect(order()).toEqual(['result:first']);
    });
  });

  describe('when it is grey', () => {
    const expectGrey = (reason: string): void => {
      expect(byId('page-history')?.getAttribute('aria-disabled')).toBe('true');
      expect(byId('page-history-toggle')?.hasAttribute('disabled')).toBe(true);
      expect(byId('page-history-reason')?.textContent).toContain(reason);
      expect(byId('page-history-count')).toBeNull();
      expect(byId('page-history-list')).toBeNull();
    };

    it('says no page is chosen, and reads nothing', async () => {
      open();
      await render({ noPage: true, step: null });

      expectGrey('No page is chosen.');
      expect(sdk.history).not.toHaveBeenCalled();
      expect(sdk.versions).not.toHaveBeenCalled();
    });

    it('says the recipe is not saved for a step with no id, and reads nothing, not even the results of the stage', async () => {
      open();
      await render({ step: { stepId: null, processorKey: 'geometry.deskew' } });

      expectGrey('not saved yet');
      expect(sdk.history).not.toHaveBeenCalled();
      expect(sdk.versions).not.toHaveBeenCalled();
    });

    it('says nothing has happened on the page yet, when there is no change and no result', async () => {
      open();
      await render();

      expectGrey('Nothing has happened on this page yet.');
    });

    it('says the same for a stage with no result and no step open', async () => {
      open();
      await render({ step: null });

      expectGrey('Nothing has happened on this page yet.');
    });

    it('stays shut for a page with no change when the reader chose to open it, and shows no list or clear', async () => {
      open();
      await render();

      expect(byId('page-history-clear')).toBeNull();
      expect(byId('page-history-filter')).toBeNull();
    });

    it('is not grey while the lists are being read, and shows no count', async () => {
      sdk.history.mockReturnValue(new Promise(() => undefined));
      await render();

      expect(byId('page-history')?.getAttribute('aria-disabled')).toBe('false');
      expect(byId('page-history-count')).toBeNull();
    });
  });

  it('says the history could not be read when the server refuses', async () => {
    sdk.history.mockRejectedValue(new ProblemError('Broken.', 500, null, []));
    await render();

    expect(container.textContent).toContain('could not be read');
  });

  it('shows a refused history once, in its own paragraph and not again as an error of the server', async () => {
    sdk.history.mockRejectedValue(new ProblemError('Broken.', 500, null, []));
    await render();

    expect(container.textContent?.match(/could not be read/g)).toHaveLength(1);
    expect(container.textContent).not.toContain('Broken.');
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
