import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageEditSchema, ScanSchema } from '@/api';
import type { EditorScene } from '@/features/editors/scene';
import type { EditorSession } from '@/features/editors/session';
import { useEditorSession } from '@/features/editors/useEditorSession';
import { SourceKind } from '@/features/processing/compare';
import {
  autoSplit,
  deskew,
  processing,
  processor,
  recipe,
  scan,
  spread,
  step,
  version,
} from '@/features/processing/fixtures';
import { images, page, row } from '@/features/workspace/fixtures';
import { joinRows, type StripItem } from '@/features/workspace/strip';

/**
 * The page editor of a stage as the screen sees it: when there is one, what a save sends and when the stage is run
 * after it, and what "Auto" and Ctrl+Z send.
 *
 * The generated client is replaced by functions the test reads, so every request is seen as the server gets it. The
 * editor is driven through the field of the angle in the panel, which is the part of an editor that needs no canvas.
 */

const sdk = vi.hoisted(() => ({
  edits: vi.fn(),
  put: vi.fn(),
  remove: vi.fn(),
  run: vi.fn(),
  jobs: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGet: sdk.edits,
  putEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyPut: sdk.put,
  deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyDelete: sdk.remove,
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

// A stand-in for the canvas of the split line, which needs a real viewer: it shows the shape it was given and lets a
// button hand back a line, as the canvas does when an end is let go
vi.mock('@/features/editors/LineCanvas', () => ({
  LineCanvas: ({
    shape,
    onCommit,
  }: {
    shape: unknown;
    onCommit: (line: { start: { x: number; y: number }; end: { x: number; y: number } }) => void;
  }) => (
    <button
      type="button"
      data-testid="commit-line"
      data-shape={JSON.stringify(shape)}
      onClick={() => onCommit({ start: { x: 10, y: 0 }, end: { x: 12, y: 600 } })}
    >
      line
    </button>
  ),
}));

const SCENE = {} as EditorScene;

const BEFORE = { kind: SourceKind.Iiif, url: '/before/info.json' } as const;
const JOB = {
  id: 'j',
  state: 'running',
  kind: 'run-stage',
  progress: { done: 0, total: 1, fraction: 0 },
};

function edit(overrides: Partial<PageEditSchema>): PageEditSchema {
  return {
    page_id: 'page',
    stage: 'geometry',
    processor_key: 'geometry.deskew',
    kind: 'rotation',
    geometry: { degrees: 1.5 },
    mask: null,
    edit_hash: 'abc',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

function listOf(...items: PageEditSchema[]) {
  return { data: { items, total: items.length, page: 1, size: 50, pages: 1 } };
}

function jobsOf(...items: unknown[]) {
  return { data: { items, total: items.length, page: 1, size: 20, pages: 1 } };
}

describe('useEditorSession', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let session: EditorSession | null;

  interface Setup {
    state?: ReturnType<typeof processing>;
    items?: readonly StripItem[];
    current?: StripItem;
    scans?: readonly ScanSchema[];
    before?: typeof BEFORE | null;
    /** Whether to draw the canvas of the editor too, which only the split line has a stand-in for. */
    canvas?: boolean;
  }

  const PAGE_ITEM = joinRows(
    [page('page')],
    [row('page', { version: version('v', { data: { angle: -2.4, confidence: 0.18 } }) })],
  );

  function Harness({ setup }: { setup: Setup }): React.JSX.Element {
    const items = setup.items ?? PAGE_ITEM;
    session = useEditorSession({
      processing: setup.state ?? processing(),
      current: setup.current ?? items[0],
      items,
      scans: setup.scans ?? [],
      before: setup.before === undefined ? BEFORE : setup.before,
    });
    return (
      <div>
        {session?.renderPanel()}
        {setup.canvas === true ? session?.renderCanvas(SCENE) : null}
        <output data-testid="state">
          {session === null
            ? 'none'
            : JSON.stringify({ edit: session.hasEdit, busy: session.busy })}
        </output>
      </div>
    );
  }

  async function render(setup: Setup = {}): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Harness setup={setup} />
        </QueryClientProvider>,
      );
    });
    // The edits and the jobs of the book are read, which takes a few turns of the queue
    await settle();
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const field = (): HTMLInputElement | null => container.querySelector('input');

  /** Type an angle into the field and leave it with Enter, as a reader does. */
  async function typeAngle(text: string): Promise<void> {
    const input = field();
    if (input === null) {
      throw new Error('The angle field is not there.');
    }
    await act(async () => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, text);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    await settle();
    await settle();
  }

  async function pressUndo(): Promise<void> {
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true }),
      );
    });
    await settle();
    await settle();
  }

  const state = (): string => container.querySelector('[data-testid="state"]')?.textContent ?? '';

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const mock of Object.values(sdk)) {
      mock.mockReset();
    }
    sdk.edits.mockResolvedValue(listOf());
    sdk.put.mockResolvedValue({ data: edit({}) });
    sdk.remove.mockResolvedValue({ data: undefined });
    sdk.run.mockResolvedValue({ data: JOB });
    sdk.jobs.mockResolvedValue(jobsOf());
    session = null;
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

  it('has no editor for a stage whose processors offer none', async () => {
    await render({ state: processing({ catalogue: [processor('geometry.crop')] }) });

    expect(state()).toBe('none');
  });

  it('has no editor while the picture it lies on is not there', async () => {
    await render({
      items: joinRows([page('page', { images: null })], [row('page')]),
      before: null,
    });

    expect(state()).toBe('none');
  });

  it('starts from the angle the step found when the page has no edit, with the editor shut', async () => {
    await render();

    expect(session?.active).toBe(false);
    expect(session?.hasEdit).toBe(false);
    expect(field()?.value).toBe('-2.4');
    expect(session?.picture).toEqual(BEFORE);
  });

  it('opens and shuts, and opens for the page it was opened on only', async () => {
    await render();

    act(() => session?.open());
    expect(session?.active).toBe(true);

    await render({
      items: joinRows([page('other')], [row('other')]),
    });
    expect(session?.active).toBe(false);
  });

  it('saves the typed angle as a rotation of the processor and runs the stage on the one page', async () => {
    await render();

    await typeAngle('2.5');

    expect(sdk.put).toHaveBeenCalledTimes(1);
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: {
        project_id: 'project',
        page_id: 'page',
        stage: 'geometry',
        processor_key: 'geometry.deskew',
      },
      body: { kind: 'rotation', geometry: '{"degrees":2.5}' },
    });
    expect(sdk.run).toHaveBeenCalledTimes(1);
    expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { recipe_id: 'r1', page_ids: ['page'] },
    });
  });

  it('saves nothing for text that is not an angle or for the angle it already has', async () => {
    await render();

    await typeAngle('abc');
    await typeAngle('-2.4');

    expect(sdk.put).not.toHaveBeenCalled();
    expect(sdk.run).not.toHaveBeenCalled();
  });

  it('keeps the run back while another job of the book is going, then makes it once', async () => {
    sdk.jobs.mockResolvedValue(jobsOf(JOB));
    await render();

    await typeAngle('1');
    await typeAngle('2');

    expect(sdk.put).toHaveBeenCalledTimes(2);
    expect(sdk.run).not.toHaveBeenCalled();

    sdk.jobs.mockResolvedValue(jobsOf());
    await act(async () => {
      await client.invalidateQueries();
    });
    await settle();

    expect(sdk.run).toHaveBeenCalledTimes(1);
  });

  it('follows the recipe on screen, and runs that recipe, not another one that also has the processor', async () => {
    const other = recipe('r2', { steps: [step('geometry.deskew')] });
    const shown = recipe('r1', { steps: [step('geometry.crop'), step('geometry.deskew')] });
    await render({
      state: processing({
        catalogue: [processor('geometry.crop'), deskew()],
        recipe: shown,
        recipes: [other, shown],
      }),
    });

    await typeAngle('3');

    expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({ body: { recipe_id: 'r1' } });
  });

  it('has no editor when the recipe on screen does not use a processor that offers one', async () => {
    const shown = recipe('r1', { steps: [step('geometry.crop')] });
    const other = recipe('r2', { steps: [step('geometry.deskew')] });
    await render({
      state: processing({
        catalogue: [processor('geometry.crop'), deskew()],
        recipe: shown,
        recipes: [shown, other],
      }),
    });

    expect(state()).toBe('none');
  });

  it('has no editor for a step of the recipe that is switched off', async () => {
    const shown = recipe('r1', { steps: [step('geometry.deskew', { enabled: false })] });
    await render({ state: processing({ recipe: shown, recipes: [shown] }) });

    expect(state()).toBe('none');
  });

  it('says why a save failed and goes on', async () => {
    sdk.put.mockRejectedValue(new Error('The shape does not fit.'));
    await render();
    act(() => session?.open());

    await typeAngle('3');

    expect(session?.error).not.toBeNull();
    expect(sdk.run).not.toHaveBeenCalled();
  });

  describe('with an edit saved', () => {
    beforeEach(() => {
      sdk.edits.mockResolvedValue(listOf(edit({ geometry: { degrees: 1.5 } })));
    });

    it('starts from the saved angle and says the page has an edit', async () => {
      await render();

      expect(session?.hasEdit).toBe(true);
      expect(field()?.value).toBe('1.5');
    });

    it('deletes the edit and runs the stage again for Auto', async () => {
      await render();

      await act(async () => session?.auto());
      await settle();
      await settle();

      expect(sdk.remove).toHaveBeenCalledTimes(1);
      expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'page', processor_key: 'geometry.deskew' },
      });
      expect(sdk.run).toHaveBeenCalledTimes(1);
    });

    it('takes back a change with Ctrl+Z by putting the edit the page had before', async () => {
      await render();
      act(() => session?.open());
      await typeAngle('4');
      sdk.put.mockClear();

      await pressUndo();

      expect(sdk.put).toHaveBeenCalledTimes(1);
      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        body: { kind: 'rotation', geometry: '{"degrees":1.5}' },
      });
    });

    it('takes back Auto with Ctrl+Z by putting the deleted edit back', async () => {
      await render();
      act(() => session?.open());
      await act(async () => session?.auto());
      await settle();
      await settle();

      await pressUndo();

      expect(sdk.put).toHaveBeenCalledTimes(1);
      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        body: { geometry: '{"degrees":1.5}' },
      });
    });

    it('answers Ctrl+Z only while the editor is open', async () => {
      await render();
      await typeAngle('4');
      sdk.put.mockClear();

      await pressUndo();

      expect(sdk.put).not.toHaveBeenCalled();
    });
  });

  it('takes back the first change of a page that had no edit by deleting the edit', async () => {
    await render();
    act(() => session?.open());
    await typeAngle('4');

    await pressUndo();

    expect(sdk.remove).toHaveBeenCalledTimes(1);
  });

  describe('the split line', () => {
    const SCAN = scan('s1', 1000, 600);
    const state = processing({
      stage: 'page-split',
      catalogue: [spread()],
      recipes: [recipe('cut', { stage: 'page-split', steps: [step('split.spread')] })],
      recipe: recipe('cut', { stage: 'page-split', steps: [step('split.spread')] }),
    });

    function pages(slots: readonly number[]): StripItem[] {
      return joinRows(
        slots.map((slot, index) =>
          page(`p${index}`, { scan_id: 's1', slot, position: index, images: images(`p${index}`) }),
        ),
        slots.map((_, index) => row(`p${index}`, { version: version(`v${index}`) })),
      );
    }

    it('lies on the scan, in the pixels of the scan, and belongs to the left page', async () => {
      const items = pages([1, 2]);
      sdk.edits.mockResolvedValue(
        listOf(
          edit({
            page_id: 'p0',
            stage: 'page-split',
            processor_key: 'split.spread',
            kind: 'line',
            geometry: { start: { x: 480, y: 0 }, end: { x: 500, y: 600 } },
          }),
        ),
      );

      await render({ state, items, current: items[1], scans: [SCAN], before: null, canvas: true });

      expect(session?.alwaysOn).toBe(true);
      expect(session?.active).toBe(true);
      expect(session?.picture).toEqual({ kind: SourceKind.Iiif, url: '/scan-s1/info.json' });
      expect(session?.hasEdit).toBe(true);
      expect(sdk.edits.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p0', stage: 'page-split' },
      });
    });

    it('has no editor for a scan whose pictures are not cut yet', async () => {
      const items = pages([1, 2]);
      const uncut: ScanSchema = { ...SCAN, images: null };

      await render({ state, items, current: items[0], scans: [uncut], before: null, canvas: true });

      expect(session).toBeNull();
    });

    const commitLine = async (): Promise<void> => {
      await act(async () => {
        container.querySelector<HTMLElement>('[data-testid="commit-line"]')?.click();
      });
      await settle();
      await settle();
    };

    it('saves a line moved on a split scan and cuts the scan again from the left page', async () => {
      const items = pages([1, 2]);
      await render({ state, items, current: items[1], scans: [SCAN], before: null, canvas: true });

      await commitLine();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p0', stage: 'page-split', processor_key: 'split.spread' },
        body: {
          kind: 'line',
          geometry: '{"start":{"x":10,"y":0},"end":{"x":12,"y":600}}',
        },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { recipe_id: 'cut', page_ids: ['p0'] },
      });
    });

    it('does not cut a scan that is kept whole when its line is moved', async () => {
      const items = pages([0]);
      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      await commitLine();

      expect(sdk.put).toHaveBeenCalledTimes(1);
      expect(sdk.run).not.toHaveBeenCalled();
    });

    it('starts the line where the step cut when the page has no edit', async () => {
      const items = joinRows(
        [page('p0', { scan_id: 's1', slot: 1 }), page('p1', { scan_id: 's1', slot: 2 })],
        [
          row('p0', { version: version('v0', { data: { cut_x: 420 } }) }),
          row('p1', { version: version('v1', { data: { cut_x: 420 } }) }),
        ],
      );

      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      expect(
        container.querySelector('[data-testid="commit-line"]')?.getAttribute('data-shape'),
      ).toBe('{"start":{"x":420,"y":0},"end":{"x":420,"y":600}}');
    });
  });

  describe('the split on a book cut by the automatic split', () => {
    const SCAN = scan('s1', 1000, 600);
    // The cutting of a spread comes first in the catalogue, which must not make it the editor of a book on split.auto
    const catalogue = [spread(), autoSplit()];
    const auto = recipe('auto', { stage: 'page-split', steps: [step('split.auto')] });
    const state = processing({ stage: 'page-split', catalogue, recipes: [auto], recipe: auto });

    function pages(slots: readonly number[], data: Record<string, unknown> = {}): StripItem[] {
      return joinRows(
        slots.map((slot, index) =>
          page(`p${index}`, { scan_id: 's1', slot, position: index, images: images(`p${index}`) }),
        ),
        slots.map((_, index) => row(`p${index}`, { version: version(`v${index}`, { data }) })),
      );
    }

    const shapeShown = (): string | null | undefined =>
      container.querySelector('[data-testid="commit-line"]')?.getAttribute('data-shape');

    const commitLine = async (): Promise<void> => {
      await act(async () => {
        container.querySelector<HTMLElement>('[data-testid="commit-line"]')?.click();
      });
      await settle();
      await settle();
    };

    it('saves the moved line as two pages cut along it, and runs the automatic split on the left page', async () => {
      const items = pages([1, 2]);
      await render({ state, items, current: items[1], scans: [SCAN], before: null, canvas: true });

      await commitLine();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p0', stage: 'page-split', processor_key: 'split.auto' },
        body: {
          kind: 'split',
          geometry: '{"pages":2,"line":{"start":{"x":10,"y":0},"end":{"x":12,"y":600}}}',
        },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { recipe_id: 'auto', page_ids: ['p0'] },
      });
    });

    it('cuts a scan that was kept whole when its line is moved, since the line is a choice of two pages', async () => {
      const items = pages([0]);
      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      await commitLine();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({ body: { kind: 'split' } });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { recipe_id: 'auto', page_ids: ['p0'] },
      });
    });

    it('starts the line where the step cut, slanted as it was found, when the page has no edit', async () => {
      const items = pages([1, 2], { cut_x: 500, cut_top_x: 460, cut_bottom_x: 540 });

      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      expect(shapeShown()).toBe('{"start":{"x":460,"y":0},"end":{"x":540,"y":600}}');
      expect(session?.hasEdit).toBe(false);
    });

    it('shows the cut the step found for a choice that has no line of its own', async () => {
      const items = pages([1, 2], { cut_x: 420 });
      sdk.edits.mockResolvedValue(
        listOf(
          edit({
            page_id: 'p0',
            stage: 'page-split',
            processor_key: 'split.auto',
            kind: 'split',
            geometry: { pages: 2, line: null },
          }),
        ),
      );

      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      expect(session?.hasEdit).toBe(true);
      expect(shapeShown()).toBe('{"start":{"x":420,"y":0},"end":{"x":420,"y":600}}');
    });

    it('shows the line a reader drew', async () => {
      const items = pages([1, 2], { cut_x: 420 });
      sdk.edits.mockResolvedValue(
        listOf(
          edit({
            page_id: 'p0',
            stage: 'page-split',
            processor_key: 'split.auto',
            kind: 'split',
            geometry: { pages: 2, line: { start: { x: 300, y: 0 }, end: { x: 310, y: 600 } } },
          }),
        ),
      );

      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      expect(shapeShown()).toBe('{"start":{"x":300,"y":0},"end":{"x":310,"y":600}}');
    });

    it('keeps the line editor for a book on the older cutting of a spread, with the same catalogue', async () => {
      const cut = recipe('cut', { stage: 'page-split', steps: [step('split.spread')] });
      const items = pages([1, 2]);
      await render({
        state: processing({ stage: 'page-split', catalogue, recipes: [cut], recipe: cut }),
        items,
        current: items[1],
        scans: [SCAN],
        before: null,
        canvas: true,
      });

      await commitLine();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p0', processor_key: 'split.spread' },
        body: { kind: 'line' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({ body: { recipe_id: 'cut' } });
    });

    it('deletes the choice and runs the automatic split again on "Auto"', async () => {
      const items = pages([1, 2]);
      sdk.edits.mockResolvedValue(
        listOf(
          edit({
            page_id: 'p0',
            stage: 'page-split',
            processor_key: 'split.auto',
            kind: 'split',
            geometry: { pages: 2, line: { start: { x: 300, y: 0 }, end: { x: 310, y: 600 } } },
          }),
        ),
      );
      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      await act(async () => session?.auto());
      await settle();
      await settle();

      expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p0', processor_key: 'split.auto' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({ body: { recipe_id: 'auto' } });
    });
  });

  it('is built for a processor of the stage even when the recipe has another step first', async () => {
    const state = processing({ catalogue: [processor('geometry.crop'), deskew()] });

    await render({ state });

    expect(session).not.toBeNull();
  });
});
