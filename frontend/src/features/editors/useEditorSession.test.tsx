import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageEditSchema, ScanSchema } from '@/api';
import type { EditorScene } from '@/features/editors/scene';
import type { EditorSession } from '@/features/editors/session';
import { useEditorSession } from '@/features/editors/useEditorSession';
import { type ImageSource, SourceKind } from '@/features/processing/compare';
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
import { usePageHistory } from '@/features/processing/historyQueries';
import { images, page, row } from '@/features/workspace/fixtures';
import { joinRows, type StripItem } from '@/features/workspace/strip';
import { ProblemError } from '@/shared/http/problem';

/**
 * The page editor of a stage as the screen sees it: when there is one, what a save sends and when the stage is run
 * after it, and what "Auto" and Ctrl+Z send.
 *
 * The generated client is replaced by functions the test reads, so every request is seen as the server gets it. The
 * editor is driven through the field of the angle in the panel, which is the part of an editor that needs no canvas.
 */

/** The angle the stand-in canvas of the angle asks to save after a pause, one key press from what the step found. */
const NUDGED_DEGREES = 1.45;

/** The quiet time after the last small step of a shape before it is saved. */
const NUDGE_SAVE_DELAY_MS = 600;

/** The line the stand-in canvas of the split line hands back when a test presses it. */
const handed = vi.hoisted(() => ({
  line: { start: { x: 10, y: 0 }, end: { x: 12, y: 600 } },
}));

const sdk = vi.hoisted(() => ({
  edits: vi.fn(),
  put: vi.fn(),
  remove: vi.fn(),
  run: vi.fn(),
  undo: vi.fn(),
  jobs: vi.fn(),
  versions: vi.fn(),
  settings: vi.fn(),
  putSetting: vi.fn(),
  history: vi.fn(),
  preview: vi.fn(),
  job: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGet: sdk.edits,
  putEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdPut: sdk.put,
  deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdDelete: sdk.remove,
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGet: sdk.settings,
  putValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNamePut: sdk.putSetting,
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
  previewStepApiV1ProjectsProjectIdStagesStagePreviewPost: sdk.preview,
  readJobApiV1JobsJobIdGet: sdk.job,
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
      onClick={() => onCommit(handed.line)}
    >
      line
    </button>
  ),
}));

// The canvas of the sheet needs a real viewer too, and the steps of the stage are shown one at a time
vi.mock('@/features/editors/QuadCanvas', () => ({
  QuadCanvas: () => <span data-testid="quad-canvas" />,
}));

// A stand-in for the canvas of the frame, in the same way
vi.mock('@/features/editors/RectCanvas', () => ({
  RectCanvas: ({
    shape,
    onCommit,
  }: {
    shape: unknown;
    onCommit: (frame: { left: number; top: number; width: number; height: number }) => void;
  }) => (
    <button
      type="button"
      data-testid="commit-rect"
      data-shape={JSON.stringify(shape)}
      onClick={() => onCommit({ left: 50, top: 60, width: 700, height: 900 })}
    >
      frame
    </button>
  ),
}));

// A stand-in for the canvas of the content box, in the same way
vi.mock('@/features/editors/MarginsCanvas', () => ({
  MarginsCanvas: ({
    shape,
    figure,
    onCommit,
  }: {
    shape: unknown;
    figure: string;
    onCommit: (box: { left: number; top: number; width: number; height: number }) => void;
  }) => (
    <button
      type="button"
      data-testid="commit-box"
      data-figure={figure}
      data-shape={JSON.stringify(shape)}
      onClick={() => onCommit({ left: 20, top: 30, width: 600, height: 900 })}
    >
      box
    </button>
  ),
}));

// A stand-in for the canvas of the angle, which needs a real viewer too: a button asks for the save that waits for a
// pause of an angle the arrow keys made, as the canvas does
vi.mock('@/features/editors/RotationCanvas', () => ({
  RotationCanvas: ({ onCommitLater }: { onCommitLater: (angle: { degrees: number }) => void }) => (
    <button
      type="button"
      data-testid="nudge-angle"
      onClick={() => onCommitLater({ degrees: NUDGED_DEGREES })}
    >
      nudge
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
    step_id: 'id-geometry.deskew',
    kind: 'rotation',
    geometry: { degrees: 1.5 },
    mask: null,
    edit_hash: 'abc',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

/** What an undo of the server answers: the undos it wrote, one of the layer given. */
function undone(layer: 'settings' | 'hand') {
  return {
    data: {
      changes: [
        {
          id: 'undo',
          page_id: 'page',
          stage: 'geometry',
          step_id: 'id-geometry.deskew',
          layer,
          before: null,
          after: null,
          source: 'undo',
          batch_id: null,
          undoes: 'change',
          created_at: '2026-10-01T00:00:00Z',
          sequence: 2,
        },
      ],
    },
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
    before?: ImageSource | null;
    /** Whether to draw the canvas of the editor too, which only the split line has a stand-in for. */
    canvas?: boolean;
    /** The step open in the step workspace. */
    focusStepId?: string;
    serverFigure?: 'default' | 'found' | 'by-hand' | 'skipped' | null;
  }

  const PAGE_ITEM = joinRows(
    [page('page')],
    [row('page', { version: version('v', { data: { angle: -2.4, confidence: 0.18 } }) })],
  );

  function Harness({ setup }: { setup: Setup }): React.JSX.Element {
    // The history of the step on the page, which stands on screen beside the editor and is read again after a save
    usePageHistory('project', 'page', 'geometry', 'id-geometry.deskew');
    const items = setup.items ?? PAGE_ITEM;
    session = useEditorSession({
      processing: setup.state ?? processing(),
      current: setup.current ?? items[0],
      items,
      scans: setup.scans ?? [],
      before: setup.before === undefined ? BEFORE : setup.before,
      focusStepId: setup.focusStepId,
      serverFigure: setup.serverFigure,
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
    // Radix measures the thumb of the slider of the angle, which jsdom cannot
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
      },
    );
    for (const mock of Object.values(sdk)) {
      mock.mockReset();
    }
    handed.line = { start: { x: 10, y: 0 }, end: { x: 12, y: 600 } };
    sdk.edits.mockResolvedValue(listOf());
    sdk.put.mockResolvedValue({ data: edit({}) });
    sdk.remove.mockResolvedValue({ data: undefined });
    sdk.run.mockResolvedValue({ data: JOB });
    sdk.undo.mockResolvedValue(undone('hand'));
    sdk.jobs.mockResolvedValue(jobsOf());
    sdk.settings.mockResolvedValue({
      data: { items: [], total: 0, page: 1, size: 50, pages: 1 },
    });
    sdk.putSetting.mockResolvedValue({
      data: { batch_id: 'batch', changes: [] },
    });
    sdk.versions.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 50, pages: 1 } });
    sdk.history.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.preview.mockResolvedValue({ data: JOB });
    sdk.job.mockResolvedValue({ data: JOB });
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

  describe('with a recipe that runs one processor twice', () => {
    const twice = recipe('twice', {
      steps: [
        step('geometry.deskew', { step_id: 'first' }),
        step('geometry.deskew', { step_id: 'second' }),
      ],
    });
    const twiceState = processing({ recipe: twice, recipes: [twice] });

    it('offers an editor for each step, titled by its kind with no number, and starts on the first', async () => {
      await render({ state: twiceState });

      expect(session?.steps.map((entry) => [entry.key, entry.title, entry.chosen])).toEqual([
        ['first', 'Angle', true],
        ['second', 'Angle', false],
      ]);
    });

    it('saves the angle for the step that was chosen, and the edit of the other step is not its edit', async () => {
      sdk.edits.mockResolvedValue(listOf(edit({ step_id: 'first', geometry: { degrees: 4 } })));
      await render({ state: twiceState });
      expect(session?.hasEdit).toBe(true);

      act(() => session?.choose('second'));
      await settle();
      expect(session?.hasEdit).toBe(false);
      await typeAngle('2.5');

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({ path: { step_id: 'second' } });
      expect(session?.steps.map((entry) => entry.manual)).toEqual([true, false]);
    });
  });

  it('has no editor on a page that did not meet the condition of the step, which passed it as it was', async () => {
    const skipped = joinRows(
      [page('page')],
      [row('page', { version: version('v', { data: { skipped_by_condition: true } }) })],
    );

    await render({ items: skipped });

    expect(state()).toBe('none');
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
        step_id: 'id-geometry.deskew',
      },
      body: { kind: 'rotation', geometry: '{"degrees":2.5}' },
    });
    expect(sdk.run).toHaveBeenCalledTimes(1);
    expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { page_ids: ['page'] },
    });
  });

  it('reads the history and the results of the page again once an edit is saved, without a reload', async () => {
    await render();
    const historyReads = sdk.history.mock.calls.length;
    const versionReads = sdk.versions.mock.calls.length;

    await typeAngle('2.5');

    expect(sdk.put).toHaveBeenCalledTimes(1);
    expect(sdk.history.mock.calls.length).toBeGreaterThan(historyReads);
    expect(sdk.versions.mock.calls.length).toBeGreaterThan(versionReads);
  });

  it('reads the history again when the edit is deleted too', async () => {
    sdk.edits.mockResolvedValue(listOf(edit({})));
    await render();
    const historyReads = sdk.history.mock.calls.length;

    await act(async () => session?.auto());
    await settle();

    expect(sdk.remove).toHaveBeenCalledTimes(1);
    expect(sdk.history.mock.calls.length).toBeGreaterThan(historyReads);
  });

  it('reads the history again when a save is refused, since the write may have changed it', async () => {
    sdk.put.mockRejectedValue(new ProblemError('Broken.', 500, null, []));
    await render();
    const historyReads = sdk.history.mock.calls.length;

    await typeAngle('2.5');

    expect(sdk.history.mock.calls.length).toBeGreaterThan(historyReads);
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

  it('asks for the run again when the server is busy with a job the list did not show yet', async () => {
    sdk.run.mockRejectedValueOnce(new ProblemError('The project is processing.', 409, null, []));
    await render();

    await typeAngle('2.5');
    await settle();
    await settle();
    await settle();

    expect(sdk.run).toHaveBeenCalledTimes(2);
    expect(session?.error).toBeNull();
  });

  it('follows the recipe on screen, and saves for its step, not for another recipe that also has the processor', async () => {
    const other = recipe('r2', { steps: [step('geometry.deskew', { step_id: 'other-deskew' })] });
    const shown = recipe('r1', {
      steps: [step('geometry.crop'), step('geometry.deskew', { step_id: 'shown-deskew' })],
    });
    await render({
      state: processing({
        catalogue: [processor('geometry.crop'), deskew()],
        recipe: shown,
        recipes: [other, shown],
      }),
    });

    await typeAngle('3');

    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({ path: { step_id: 'shown-deskew' } });
    expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({ body: { page_ids: ['page'] } });
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
        path: { page_id: 'page', step_id: 'id-geometry.deskew' },
      });
      expect(sdk.run).toHaveBeenCalledTimes(1);
    });

    it('takes back a change with Ctrl+Z by asking the server to undo the newest change of the step', async () => {
      await render();
      act(() => session?.open());
      await typeAngle('4');
      sdk.put.mockClear();

      await pressUndo();

      expect(sdk.undo).toHaveBeenCalledTimes(1);
      expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'page', stage: 'geometry', step_id: 'id-geometry.deskew' },
        body: { change_id: null },
      });
      expect(sdk.put).not.toHaveBeenCalled();
    });

    it('runs the stage again after an undo that put an edit back', async () => {
      await render();
      act(() => session?.open());
      await pressUndo();
      await settle();

      expect(sdk.run).toHaveBeenCalledTimes(1);
    });

    it('does not run the stage after an undo that took back only a setting of the page', async () => {
      sdk.undo.mockResolvedValue(undone('settings'));
      await render();
      act(() => session?.open());

      await pressUndo();
      await settle();

      expect(sdk.run).not.toHaveBeenCalled();
    });

    it('shows the answer of the server when the undo is refused', async () => {
      sdk.undo.mockRejectedValue(new ProblemError('The settings changed since.', 409, null, []));
      await render();
      act(() => session?.open());

      await pressUndo();

      expect(session?.error).toBe('The settings changed since.');
    });

    it('answers Ctrl+Z only while the editor is open', async () => {
      await render();
      await typeAngle('4');
      sdk.put.mockClear();

      await pressUndo();

      expect(sdk.undo).not.toHaveBeenCalled();
    });
  });

  describe('in the workspace of an open step', () => {
    const NO_RESULT = joinRows([page('page')], [row('page')]);
    const DESKEW_STEP = 'id-geometry.deskew';

    it('has the shape on the page without "Set by hand", and leaves the choice of the step to the screen', async () => {
      await render({ focusStepId: DESKEW_STEP });

      expect(session?.active).toBe(true);
      expect(session?.alwaysOn).toBe(true);
      expect(session?.focused).toBe(true);
    });

    it('keeps the editor shut for the same step when no step is open', async () => {
      await render();

      expect(session?.active).toBe(false);
      expect(session?.focused).toBe(false);
    });

    it('starts the shape from the default before the step has run, found after it has, and set by hand after an edit', async () => {
      await render({ focusStepId: DESKEW_STEP, items: NO_RESULT });
      expect(session?.figure).toBe('default');
      expect(field()?.value).toBe('0');

      await render({ focusStepId: DESKEW_STEP });
      expect(session?.figure).toBe('found');
      expect(field()?.value).toBe('-2.4');

      // The edits of the page were read by the renders above, and the harness keeps its cache between them, so the
      // edit is read again as it is after a save
      sdk.edits.mockResolvedValue(listOf(edit({ geometry: { degrees: 1.5 } })));
      await act(async () => {
        await client.invalidateQueries();
      });
      await render({ focusStepId: DESKEW_STEP });
      expect(session?.figure).toBe('by-hand');
      expect(field()?.value).toBe('1.5');
    });

    it('has an edit to take back, which is what Auto on the toolbar needs, only for a shape set by hand', async () => {
      await render({ focusStepId: DESKEW_STEP });
      expect(session?.figure).toBe('found');
      expect(session?.hasEdit).toBe(false);

      await render({ focusStepId: DESKEW_STEP, items: NO_RESULT });
      expect(session?.figure).toBe('default');
      expect(session?.hasEdit).toBe(false);

      // The edits of the page were read by the renders above, and the harness keeps its cache between them
      sdk.edits.mockResolvedValue(listOf(edit({ geometry: { degrees: 1.5 } })));
      await act(async () => {
        await client.invalidateQueries();
      });
      await render({ focusStepId: DESKEW_STEP });
      expect(session?.figure).toBe('by-hand');
      expect(session?.hasEdit).toBe(true);
    });

    it('takes the shape of the step back with Auto, which leaves the shape the step found', async () => {
      sdk.edits.mockResolvedValue(listOf(edit({ geometry: { degrees: 1.5 } })));
      await render({ focusStepId: DESKEW_STEP });
      expect(session?.figure).toBe('by-hand');

      // The server holds no edit once it is deleted, so the list read again is empty
      sdk.edits.mockResolvedValue(listOf());
      await act(async () => session?.auto());
      await settle();
      await settle();

      expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'page', step_id: DESKEW_STEP },
      });
      await vi.waitFor(() => expect(session?.hasEdit).toBe(false));
      expect(session?.figure).toBe('found');
    });

    it('takes the state of the shape from the row of the open step when the server has sent it', async () => {
      await render({ focusStepId: DESKEW_STEP, items: NO_RESULT, serverFigure: 'by-hand' });
      expect(session?.figure).toBe('by-hand');

      await render({ focusStepId: DESKEW_STEP, serverFigure: 'default' });
      expect(session?.figure).toBe('default');
    });

    it('shows a saved edit as set by hand at once, while the row of the step still says found', async () => {
      sdk.edits.mockResolvedValue(listOf(edit({ geometry: { degrees: 1.5 } })));

      await render({ focusStepId: DESKEW_STEP, serverFigure: 'found' });

      expect(session?.figure).toBe('by-hand');
    });

    it('applies the same rule to what the screen has while the row of the step is read, and when no step is open', async () => {
      await render({ focusStepId: DESKEW_STEP, serverFigure: null });
      expect(session?.figure).toBe('found');

      // Without an open step the server row is not asked for, so what it would say is ignored
      await render({ serverFigure: 'by-hand' });
      expect(session?.figure).toBe('found');
    });

    it('keeps the state of the shape of a step as the open step changes and comes back', async () => {
      const twice = recipe('twice', {
        steps: [
          step('geometry.deskew', { step_id: 'first' }),
          step('geometry.deskew', { step_id: 'second' }),
        ],
      });
      sdk.edits.mockResolvedValue(listOf(edit({ step_id: 'first', geometry: { degrees: 4 } })));
      const state = processing({ recipe: twice, recipes: [twice] });

      await render({ state, focusStepId: 'first', items: NO_RESULT });
      expect(session?.figure).toBe('by-hand');
      expect(field()?.value).toBe('4');

      await render({ state, focusStepId: 'second', items: NO_RESULT });
      expect(session?.figure).toBe('default');

      await render({ state, focusStepId: 'first', items: NO_RESULT });
      expect(session?.figure).toBe('by-hand');
      expect(field()?.value).toBe('4');
    });

    it('has no editor for the open step on a page it passed by, or for a step that has none', async () => {
      const skipped = joinRows(
        [page('page')],
        [row('page', { version: version('v', { data: { skipped_by_condition: true } }) })],
      );
      await render({ focusStepId: DESKEW_STEP, items: skipped });
      expect(state()).toBe('none');

      const crop = recipe('c', { steps: [step('geometry.crop')] });
      await render({
        state: processing({
          catalogue: [processor('geometry.crop')],
          recipe: crop,
          recipes: [crop],
        }),
        focusStepId: 'id-geometry.crop',
      });
      expect(state()).toBe('none');
    });

    it('starts the frame from the margins of the whole picture, read from its pyramid, before the step has run', async () => {
      const fetched = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ width: 1000, height: 2000 }),
      });
      vi.stubGlobal('fetch', fetched);
      const crop = recipe('c', { steps: [step('geometry.crop')] });
      const cropState = processing({
        catalogue: [processor('geometry.crop', { editor: 'rect' })],
        recipe: crop,
        recipes: [crop],
      });

      await render({
        state: cropState,
        focusStepId: 'id-geometry.crop',
        items: NO_RESULT,
        canvas: true,
      });
      await settle();

      expect(fetched).toHaveBeenCalledWith(BEFORE.url);
      expect(session?.figure).toBe('default');
      expect(
        container.querySelector('[data-testid="commit-rect"]')?.getAttribute('data-shape'),
      ).toBe(JSON.stringify({ left: 100, top: 200, width: 800, height: 1600 }));
    });

    it('has no frame before the step has run when no step is open, since there is nothing to start it from', async () => {
      vi.stubGlobal('fetch', vi.fn());
      const crop = recipe('c', { steps: [step('geometry.crop')] });

      await render({
        state: processing({
          catalogue: [processor('geometry.crop', { editor: 'rect' })],
          recipe: crop,
          recipes: [crop],
        }),
        items: NO_RESULT,
      });

      expect(state()).toBe('none');
    });
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
            step_id: 'id-split.spread',
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
        path: { page_id: 'p0', stage: 'page-split', step_id: 'id-split.spread' },
        body: {
          kind: 'line',
          geometry: '{"start":{"x":10,"y":0},"end":{"x":12,"y":600}}',
        },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { page_ids: ['p0'] },
      });
    });

    it('keeps the line held last on screen while the save of an older line settles', async () => {
      const items = pages([1, 2]);
      const older = { start: { x: 100, y: 0 }, end: { x: 102, y: 600 } };
      const newer = { start: { x: 200, y: 0 }, end: { x: 202, y: 600 } };
      // Each save answers when the test says so, in the order the saves were made
      const answers: ((answer: { data: PageEditSchema }) => void)[] = [];
      const held = (): Promise<{ data: PageEditSchema }> =>
        new Promise((resolve) => answers.push(resolve));
      sdk.put.mockImplementationOnce(held).mockImplementationOnce(held);
      const onServer = (line: typeof older): PageEditSchema =>
        edit({
          page_id: 'p0',
          stage: 'page-split',
          step_id: 'id-split.spread',
          kind: 'line',
          geometry: line,
        });
      await render({ state, items, current: items[1], scans: [SCAN], before: null, canvas: true });
      const shown = (): string | null | undefined =>
        container.querySelector('[data-testid="commit-line"]')?.getAttribute('data-shape');

      // Two lines are let go one after the other, and the server takes them in that order
      handed.line = older;
      await commitLine();
      handed.line = newer;
      await commitLine();
      expect(sdk.put).toHaveBeenCalledTimes(1);

      // The save of the older line settles and the edit is read again while the newer one is still on its way
      sdk.edits.mockResolvedValue(listOf(onServer(older)));
      answers[0]?.({ data: onServer(older) });
      await settle();
      await settle();
      expect(shown()).toBe(JSON.stringify(newer));

      sdk.edits.mockResolvedValue(listOf(onServer(newer)));
      answers[1]?.({ data: onServer(newer) });
      await settle();
      await settle();
      expect(sdk.put).toHaveBeenCalledTimes(2);
      expect(shown()).toBe(JSON.stringify(newer));
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
        path: { page_id: 'p0', stage: 'page-split', step_id: 'id-split.auto' },
        body: {
          kind: 'split',
          geometry: '{"pages":2,"line":{"start":{"x":10,"y":0},"end":{"x":12,"y":600}}}',
        },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { page_ids: ['p0'] },
      });
    });

    it('cuts a scan that was kept whole when its line is moved, since the line is a choice of two pages', async () => {
      const items = pages([0]);
      await render({ state, items, current: items[0], scans: [SCAN], before: null, canvas: true });

      await commitLine();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({ body: { kind: 'split' } });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { page_ids: ['p0'] },
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
            step_id: 'id-split.auto',
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
            step_id: 'id-split.auto',
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
        path: { page_id: 'p0', step_id: 'id-split.spread' },
        body: { kind: 'line' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({ body: { page_ids: ['p0'] } });
    });

    it('deletes the choice and runs the automatic split again on "Auto"', async () => {
      const items = pages([1, 2]);
      sdk.edits.mockResolvedValue(
        listOf(
          edit({
            page_id: 'p0',
            stage: 'page-split',
            step_id: 'id-split.auto',
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
        path: { page_id: 'p0', step_id: 'id-split.auto' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({ body: { page_ids: ['p0'] } });
    });
  });

  it('is built for a processor of the stage even when the recipe has another step first', async () => {
    const state = processing({ catalogue: [processor('geometry.crop'), deskew()] });

    await render({ state });

    expect(session).not.toBeNull();
  });

  describe('on a stage of three steps that each have an editor', () => {
    const SHEET = {
      top_left: { x: 20, y: 30 },
      top_right: { x: 980, y: 20 },
      bottom_right: { x: 990, y: 1380 },
      bottom_left: { x: 10, y: 1390 },
    };
    const PERSPECTIVE = version('v1', {
      processor: { key: 'geometry.perspective', version: '1' },
      input_id: 'scan-version',
      data: { quad: SHEET, source_width_px: 1000, source_height_px: 1400, confidence: 0.9 },
    });
    const DESKEW = version('v2', {
      input_id: 'v1',
      tiles_ready: false,
      data: { angle: 0.3, confidence: 0.8 },
    });
    const CROP = version('v3', {
      processor: { key: 'geometry.crop', version: '2' },
      input_id: 'v2',
      tiles_ready: false,
      data: {
        frame: { left: 60, top: 70, width: 800, height: 1200 },
        source_width_px: 970,
        source_height_px: 1360,
      },
    });
    const STATE = processing({
      catalogue: [
        processor('geometry.perspective', { editor: 'quad' }),
        deskew(),
        processor('geometry.crop', { editor: 'rect' }),
      ],
      recipe: recipe('r1', {
        steps: [step('geometry.perspective'), step('geometry.deskew'), step('geometry.crop')],
      }),
    });
    const ITEMS = joinRows([page('page')], [row('page', { version: CROP })]);

    beforeEach(() => {
      sdk.versions.mockResolvedValue({
        data: { items: [PERSPECTIVE, DESKEW, CROP], total: 3, page: 1, size: 50, pages: 1 },
      });
    });

    it('lists the steps in the order of the recipe, with the first one shown', async () => {
      await render({ state: STATE, items: ITEMS });

      expect(session?.steps.map((entry) => [entry.title, entry.chosen, entry.manual])).toEqual([
        ['Sheet corners', true, false],
        ['Angle', false, false],
        ['Content frame', false, false],
      ]);
      expect(session?.steps[1]?.detail).toBe('0.3°');
    });

    it('lies on the picture the server gives for the row of the open step, whichever step is picked', async () => {
      const read = { kind: SourceKind.Iiif, url: '/read/info.json' } as const;
      await render({ state: STATE, items: ITEMS });
      expect(session?.picture).toEqual(BEFORE);

      // The versions the page holds of the steps before are not asked for the picture
      await act(async () => session?.choose('id-geometry.crop'));
      expect(session?.picture).toEqual(BEFORE);

      await render({ state: STATE, items: ITEMS, before: read });
      expect(session?.picture).toEqual(read);
    });

    it('opens the editor of the step that is picked', async () => {
      await render({ state: STATE, items: ITEMS });
      expect(session?.active).toBe(false);

      await act(async () => session?.choose('id-geometry.crop'));

      expect(session?.active).toBe(true);
      expect(session?.steps.map((entry) => entry.chosen)).toEqual([false, false, true]);
    });

    it('has no editor for the sheet or the frame before their step has run on the page', async () => {
      sdk.versions.mockResolvedValue({
        data: { items: [], total: 0, page: 1, size: 50, pages: 1 },
      });
      const unfinished = joinRows(
        [page('page')],
        [row('page', { version: version('v0', { data: {} }) })],
      );

      await render({ state: STATE, items: unfinished });

      expect(state()).toBe('none');
    });

    it('saves the frame as a rect of the crop step and runs the stage on the one page', async () => {
      await render({ state: STATE, items: ITEMS, canvas: true });
      await act(async () => session?.choose('id-geometry.crop'));

      await act(async () => {
        container.querySelector<HTMLButtonElement>('[data-testid="commit-rect"]')?.click();
      });
      await settle();
      await settle();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'page', stage: 'geometry', step_id: 'id-geometry.crop' },
        body: { kind: 'rect', geometry: '{"left":50,"top":60,"width":700,"height":900}' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { page_ids: ['page'] },
      });
    });

    it('starts the frame from the one the step found', async () => {
      await render({ state: STATE, items: ITEMS, canvas: true });
      await act(async () => session?.choose('id-geometry.crop'));

      const shape = container
        .querySelector('[data-testid="commit-rect"]')
        ?.getAttribute('data-shape');
      expect(JSON.parse(shape ?? 'null')).toEqual({ left: 60, top: 70, width: 800, height: 1200 });
    });

    describe('after the frame was saved and the stage ran again', () => {
      const FOUND_AFTER = { left: 50, top: 60, width: 700, height: 900 };
      const MADE = version('v4', {
        processor: { key: 'geometry.crop', version: '2' },
        input_id: 'v2',
        tiles_ready: false,
        data: { frame: FOUND_AFTER, source_width_px: 970, source_height_px: 1360 },
      });
      const shapeOnCanvas = (): unknown =>
        JSON.parse(
          container.querySelector('[data-testid="commit-rect"]')?.getAttribute('data-shape') ??
            'null',
        );

      it('keeps the frame the step found while the list of versions has not caught up with the row', async () => {
        await render({ state: STATE, items: ITEMS, canvas: true });
        await act(async () => session?.choose('id-geometry.crop'));
        const picture = session?.picture;
        expect(picture).toEqual(BEFORE);

        // The row names the version the run made, which the list of versions does not hold yet
        const made = joinRows([page('page')], [row('page', { version: MADE })]);
        await render({ state: STATE, items: made, canvas: true });

        expect(session).not.toBeNull();
        expect(session?.picture).toEqual(picture);
        expect(shapeOnCanvas()).toEqual({ left: 60, top: 70, width: 800, height: 1200 });

        sdk.versions.mockResolvedValue({
          data: { items: [PERSPECTIVE, DESKEW, MADE], total: 3, page: 1, size: 50, pages: 1 },
        });
        await act(async () => {
          await client.invalidateQueries();
        });
        await settle();

        // The picture is the one the row gives, so it is not loaded again, and the frame is the one the step made
        expect(session?.picture).toEqual(picture);
        expect(shapeOnCanvas()).toEqual(FOUND_AFTER);
      });
    });

    it('says which steps the reader gave an edit of their own', async () => {
      sdk.edits.mockResolvedValue(
        listOf(
          edit({
            step_id: 'id-geometry.perspective',
            kind: 'quad',
            geometry: SHEET,
          }),
        ),
      );

      await render({ state: STATE, items: ITEMS });
      // The edits are read once the versions have told that the step has run
      await settle();

      expect(session?.steps.map((entry) => entry.manual)).toEqual([true, false, false]);
    });
  });

  describe('with the Margins step', () => {
    const margins = recipe('m', {
      steps: [step('geometry.normalize', { params: { margins_by: 'inner-outer' } })],
    });
    const MARGINS_STATE = processing({
      catalogue: [processor('geometry.normalize', { editor: 'content-box' })],
      recipe: margins,
      recipes: [margins],
    });
    const PLACED = joinRows(
      [page('page')],
      [
        row('page', {
          version: version('placed', {
            processor: { key: 'geometry.normalize', version: '2' },
            data: {
              content_box: { left: 100, top: 200, width: 300, height: 400 },
              margin_box: { left: 70, top: 150, width: 380, height: 520 },
              margin_pixels_per_mm: 4,
              source_width_px: 1000,
              source_height_px: 1500,
            },
          }),
        }),
      ],
    );

    it('starts the content box from the box the step found, and says it was found', async () => {
      await render({
        state: MARGINS_STATE,
        items: PLACED,
        focusStepId: 'id-geometry.normalize',
        serverFigure: 'found',
        canvas: true,
      });

      const box = container.querySelector('[data-testid="commit-box"]');
      expect(box?.getAttribute('data-shape')).toBe(
        JSON.stringify({ left: 100, top: 200, width: 300, height: 400 }),
      );
      expect(box?.getAttribute('data-figure')).toBe('found');
    });

    it('saves the box as an edit of the content box editor and runs the stage on the one page', async () => {
      await render({
        state: MARGINS_STATE,
        items: PLACED,
        focusStepId: 'id-geometry.normalize',
        canvas: true,
      });

      await act(async () => {
        container.querySelector<HTMLButtonElement>('[data-testid="commit-box"]')?.click();
      });
      await settle();
      await settle();

      expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'page', stage: 'geometry', step_id: 'id-geometry.normalize' },
        body: { kind: 'content-box', geometry: '{"left":20,"top":30,"width":600,"height":900}' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { page_ids: ['page'] },
      });
    });

    it('saves the alignment as a setting of the page alone and runs the stage on the page', async () => {
      await render({ state: MARGINS_STATE, items: PLACED, focusStepId: 'id-geometry.normalize' });

      await act(async () => {
        container
          .querySelector<HTMLInputElement>(
            '[data-testid="align-vertical"] input[type="radio"][value="bottom"]',
          )
          ?.click();
      });
      await settle();
      await settle();

      expect(sdk.putSetting.mock.calls[0]?.[0]).toMatchObject({
        path: { stage: 'geometry', step_id: 'id-geometry.normalize', name: 'align_vertical' },
        body: { scope: 'pages', page_ids: ['page'], value: 'bottom' },
      });
      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        body: { page_ids: ['page'] },
      });
    });

    it('asks for a preview of the open step on a page it has not run on, and starts no run', async () => {
      await render({
        state: MARGINS_STATE,
        items: joinRows([page('page')], [row('page')]),
        focusStepId: 'id-geometry.normalize',
      });

      // The preview waits for the form to stand still for 400 ms
      await vi.waitFor(() => expect(sdk.preview).toHaveBeenCalledTimes(1), { timeout: 3000 });

      expect(sdk.preview.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', stage: 'geometry' },
        body: { page_id: 'page', step_index: 0 },
      });
      expect(sdk.run).not.toHaveBeenCalled();
    });

    it('shows no editor and asks for no preview until the row of the page is known, and does both once it is', async () => {
      // A page the step has not run on starts the box from the whole picture, whose size its pyramid gives
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({ ok: true, json: async () => ({ width: 1000, height: 2000 }) }),
      );
      const open = { state: MARGINS_STATE, focusStepId: 'id-geometry.normalize', canvas: true };

      // The rows of the stage are still loading, so the page has no row
      await render({ ...open, items: joinRows([page('page')], []) });
      // The preview waits for the form to stand still for 400 ms
      await new Promise((resolve) => setTimeout(resolve, 600));

      expect(state()).toBe('none');
      expect(sdk.preview).not.toHaveBeenCalled();

      await render({ ...open, items: joinRows([page('page')], [row('page')]) });

      expect(state()).not.toBe('none');
      await vi.waitFor(() => expect(sdk.preview).toHaveBeenCalledTimes(1), { timeout: 3000 });
    });

    it('asks for no preview on a page the step has run on', async () => {
      await render({
        state: MARGINS_STATE,
        items: PLACED,
        focusStepId: 'id-geometry.normalize',
      });
      await new Promise((resolve) => setTimeout(resolve, 600));

      expect(sdk.preview).not.toHaveBeenCalled();
    });

    it('does not ask for the settings of the page for a step that has no use for them', async () => {
      await render();

      expect(sdk.settings).not.toHaveBeenCalled();
    });
  });

  describe('a shape moved by the keys, which is saved once they pause', () => {
    const OTHER_ITEMS = joinRows([page('other')], [row('other')]);

    afterEach(() => {
      vi.useRealTimers();
    });

    /** Draw the page again while the clock is the fake one, since the render of the harness waits on a real timer. */
    async function showAgain(setup: Setup): Promise<void> {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <Harness setup={setup} />
          </QueryClientProvider>,
        );
      });
    }

    const nudge = (): void =>
      act(() => container.querySelector<HTMLElement>('[data-testid="nudge-angle"]')?.click());

    /** Let the reader press an arrow key on the slider of the panel, which saves the angle it gives at once. */
    const pressOnSlider = (): void =>
      act(() => {
        const thumb = container.querySelector<HTMLElement>('[role="slider"]');
        thumb?.focus();
        thumb?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      });

    async function wait(milliseconds: number): Promise<void> {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(milliseconds);
      });
    }

    /** What the server was sent to save, as the request of the client holds it. */
    interface SavedEdit {
      path: { page_id: string };
      body: { geometry: string };
    }

    const requests = (): SavedEdit[] => sdk.put.mock.calls.map(([request]) => request);
    const savedPages = (): string[] => requests().map((request) => request.path.page_id);
    const savedAngles = (): number[] =>
      requests().map((request) => {
        const geometry: { degrees: number } = JSON.parse(request.body.geometry);
        return geometry.degrees;
      });

    it('saves it once the keys pause, and not before', async () => {
      await render({ canvas: true });
      vi.useFakeTimers();

      nudge();
      await wait(NUDGE_SAVE_DELAY_MS - 1);
      expect(sdk.put).not.toHaveBeenCalled();

      await wait(1);
      expect(savedAngles()).toEqual([NUDGED_DEGREES]);
    });

    it('is not saved over a newer angle the panel saved while it waited', async () => {
      await render({ canvas: true });
      vi.useFakeTimers();

      nudge();
      pressOnSlider();
      await wait(NUDGE_SAVE_DELAY_MS * 2);

      expect(savedAngles()).toHaveLength(1);
      expect(savedAngles()[0]).toBeCloseTo(-2.35, 5);
    });

    it('is saved for the page it was made on when the reader has turned to another page', async () => {
      await render({ canvas: true });
      vi.useFakeTimers();

      nudge();
      await showAgain({ canvas: true, items: OTHER_ITEMS });
      await wait(NUDGE_SAVE_DELAY_MS);

      expect(savedPages()).toEqual(['page']);
      expect(savedAngles()).toEqual([NUDGED_DEGREES]);
    });

    it('is saved at once, for its own page, when the reader moves a shape on the next page', async () => {
      await render({ canvas: true });
      vi.useFakeTimers();

      nudge();
      await showAgain({ canvas: true, items: OTHER_ITEMS });
      nudge();
      await wait(0);
      expect(savedPages()).toEqual(['page']);

      await wait(NUDGE_SAVE_DELAY_MS);
      expect(savedPages()).toEqual(['page', 'other']);
    });
  });
});
