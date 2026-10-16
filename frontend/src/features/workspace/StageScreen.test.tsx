import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, version } from '@/features/processing/fixtures';
import type { Processing } from '@/features/processing/useProcessing';
import { page, row } from '@/features/workspace/fixtures';
import { CompareMode } from '@/features/workspace/params';
import { StageScreen } from '@/features/workspace/StageScreen';
import { barStepsOf } from '@/features/workspace/steps';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';

/**
 * What the screen of a stage tells the compare canvas about the rows of the open step: the canvas waits for the place of
 * the two pictures only while the rows are read, and draws them without a place once the read of the rows has failed.
 *
 * The compare canvas, the layout and the page editor are replaced by stand-ins, and the workspace of the step by the
 * state the test gives it, so the test reads the props the screen hands to the canvas. The pages, the rows and the
 * summaries are read from the generated client, which the test replaces by functions that answer at once.
 */

/** The props of the compare canvas that the screen decides. */
interface CanvasProps {
  mode: CompareMode;
  placementPending?: boolean;
}

const mocks = vi.hoisted(() => ({
  compareCanvas: vi.fn<(props: CanvasProps) => void>(),
  workspace: undefined as StepWorkspace | undefined,
  processing: undefined as Processing | undefined,
}));
const sdk = vi.hoisted(() => ({ project: vi.fn(), pages: vi.fn(), rows: vi.fn(), stages: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  projectApiV1ProjectsProjectIdGet: sdk.project,
  listPagesApiV1ProjectsProjectIdPagesGet: sdk.pages,
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet: sdk.rows,
  listStagesApiV1ProjectsProjectIdStagesGet: sdk.stages,
}));
vi.mock('@/features/processing/CompareCanvas', () => ({
  CompareCanvas: (props: CanvasProps) => {
    mocks.compareCanvas(props);
    return null;
  },
}));
vi.mock('@/features/processing/StageBanners', () => ({ StageBanners: () => null }));
vi.mock('@/features/workspace/CanvasToolbar', () => ({ CanvasToolbar: () => null }));
vi.mock('@/features/workspace/StageWorkspace', () => ({
  StageWorkspace: ({ canvas }: { canvas: ReactNode }) => <>{canvas}</>,
}));
vi.mock('@/features/processing/useProcessing', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/processing/useProcessing')>()),
  useProcessing: () => mocks.processing,
}));
vi.mock('@/features/workspace/useStepWorkspace', () => ({
  useStepWorkspace: () => mocks.workspace,
}));
vi.mock('@/features/editors/useEditorSession', () => ({ useEditorSession: () => null }));

const PROJECT_ID = 'project';
const LIST = { total: 1, page: 1, size: 1000, pages: 1 };
const SAVED = recipe('r1');
const BAR = barStepsOf(SAVED, []);
const OPEN_STEP = BAR[0];
if (OPEN_STEP === undefined) {
  throw new Error('The recipe of the test has no step.');
}
// The page has a picture before the step and a result of the step, which are two images
const ROW = row('p1', { picture: version('before'), version: version('after') });

/** The state of the rows of the open step that the workspace reports, and what the canvas is told of it. */
interface RowsCase {
  title: string;
  rowsPending: boolean;
  placementPending: boolean;
}

const ROWS_CASES: readonly RowsCase[] = [
  { title: 'waits for the place while the rows are read', rowsPending: true, placementPending: true },
  {
    title: 'stops waiting once the read of the rows has failed',
    rowsPending: false,
    placementPending: false,
  },
];

describe('StageScreen', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** Read the props the screen handed to the compare canvas the last time it drew it. */
  const lastProps = (): CanvasProps | undefined => mocks.compareCanvas.mock.lastCall?.[0];

  /** Draw the screen of the Geometry stage with the compare open on the first step. */
  async function render(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <StageScreen
            projectId={PROJECT_ID}
            stage="geometry"
            search={{ compare: CompareMode.Swipe }}
            stepId={OPEN_STEP.stepId}
            onSearchChange={() => undefined}
            onStepChange={() => undefined}
          />
        </QueryClientProvider>,
      );
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    mocks.compareCanvas.mockReset();
    mocks.processing = processing({ projectId: PROJECT_ID, recipes: [SAVED], recipe: SAVED });
    sdk.project.mockReset().mockResolvedValue({ data: { id: PROJECT_ID } });
    sdk.pages.mockReset().mockResolvedValue({ data: { ...LIST, items: [page('p1')] } });
    sdk.rows.mockReset().mockResolvedValue({ data: { ...LIST, items: [ROW] } });
    sdk.stages.mockReset().mockResolvedValue({ data: { ...LIST, items: [] } });
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

  it.each(ROWS_CASES)('$title', async ({ rowsPending, placementPending }) => {
    mocks.workspace = {
      steps: BAR,
      open: OPEN_STEP,
      states: new Map(),
      page: null,
      // The rows stay null in both cases: they are not read yet, or their read failed and they never come
      rows: null,
      rowsPending,
      carried: null,
      setCarried: () => undefined,
    };

    await render();

    // The canvas compares the two pictures once the page and its row are read, and only then does the screen decide
    await vi.waitFor(() => expect(lastProps()?.mode).toBe(CompareMode.Swipe));
    expect(lastProps()?.placementPending).toBe(placementPending);
  });
});
