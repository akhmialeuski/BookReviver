import { QueryClient } from '@tanstack/react-query';
import {
  createMemoryHistory,
  createRootRouteWithContext,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { processor, recipe, step } from '@/features/processing/fixtures';
import { redirectToDefaultStep } from '@/features/workspace/defaultStep';
import { Route as stageRoute } from '@/routes/_authenticated/projects/$projectId/stages.$stage';

/**
 * The address of a stage with a step bar that names no step: the router sends it to the step the stage opens on, in place
 * of the address, and a navigation started before the data has been read is not overwritten by that redirect.
 *
 * The router is the real one over a memory history, whose entries are read from the array it was given. The stage route is
 * the one of the application, with the screens stubbed out, and the server is the SDK the queries call.
 */

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const sdk = vi.hoisted(() => ({ processors: vi.fn(), recipes: vi.fn(), rows: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listProcessorsApiV1ProcessorsGet: sdk.processors,
  listRecipesApiV1ProjectsProjectIdStagesStageRecipesGet: sdk.recipes,
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet: sdk.rows,
}));
vi.mock('@/features/workspace/StageScreen', () => ({
  StageScreen: () => <p data-testid="stage-screen" />,
}));
vi.mock('@/features/import/ImportScreen', () => ({ ImportScreen: () => null }));
vi.mock('@/features/order/OrderScreen', () => ({ OrderScreen: () => null }));
vi.mock('@/features/place/PlaceWriterContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/place/PlaceWriterContext')>()),
  useBookPlaceWriter: () => null,
}));

const STAGE_PATH = '/projects/book/stages/geometry';
const VIEWER_PATH = '/projects/book/viewer';
const STEP_ROUTE = '/projects/$projectId/stages/$stage/steps/$stepId';

/** With no page run yet, the stage opens on the last step of the recipe. */
const DEFAULT_STEP = 'step-2';

const LIST = { total: 1, page: 1, size: 100, pages: 1 };
const PROCESSORS = { data: { ...LIST, items: [processor('geometry.deskew')] } };
const RECIPES = {
  data: {
    ...LIST,
    items: [
      recipe('r1', {
        steps: [
          step('geometry.deskew', { step_id: 'step-1' }),
          step('geometry.normalize', { step_id: DEFAULT_STEP }),
        ],
      }),
    ],
  },
};
const NO_ROWS = { data: { items: [], total: 0, page: 1, size: 1000, pages: 1 } };

/** The longest the split chunk of the route takes to import, which pulls in the whole workspace. */
const CHUNK_IMPORT_TIMEOUT_MS = 60_000;

describe('the route of a stage', () => {
  let container: HTMLDivElement;
  let root: Root;

  // The build splits the component of a route into a chunk of its own, which the router imports before it draws, so
  // the chunk is imported once here and the tests wait only for the router
  beforeAll(async () => {
    await stageRoute.options.component?.preload?.();
  }, CHUNK_IMPORT_TIMEOUT_MS);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('scrollTo', vi.fn());
    sdk.processors.mockReset().mockResolvedValue(PROCESSORS);
    sdk.recipes.mockReset().mockResolvedValue(RECIPES);
    sdk.rows.mockReset().mockResolvedValue(NO_ROWS);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  /**
   * Build the routes of the application around a stage: its address with no step, its step, and a stand-in for the reading
   * mode whose loader is held until the test lets it go.
   *
   * The loader of the address with no step is the one line the route of the application has, with the promises it
   * returns kept so a test can tell when a redirect has been thrown.
   */
  function mount(entries: string[], viewerLoader: Deferred<void>) {
    const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()();
    const layout = createRoute({ getParentRoute: () => rootRoute, id: '/_authenticated' });
    const project = createRoute({ getParentRoute: () => layout, path: '/projects/$projectId' });
    const stage = createRoute({
      getParentRoute: () => project,
      path: '/stages/$stage',
      validateSearch: stageRoute.options.validateSearch,
      component: stageRoute.options.component,
    });
    const runs: Promise<void>[] = [];
    const withoutStep = createRoute({
      getParentRoute: () => stage,
      path: '/',
      loaderDeps: ({ search }) => ({ page: search.page }),
      loader: ({ context, params, deps }) => {
        const run = redirectToDefaultStep(context.queryClient, params, deps.page);
        runs.push(run);
        return run;
      },
    });
    const withStep = createRoute({ getParentRoute: () => stage, path: '/steps/$stepId' });
    const viewer = createRoute({
      getParentRoute: () => project,
      path: '/viewer',
      loader: () => viewerLoader.promise,
      component: () => <p data-testid="viewer" />,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      routeTree: rootRoute.addChildren([
        layout.addChildren([
          project.addChildren([stage.addChildren([withoutStep, withStep]), viewer]),
        ]),
      ]),
      history: createMemoryHistory({ initialEntries: entries }),
      context: { queryClient },
    });
    return { router, runs };
  }

  const shown = (testId: string): boolean =>
    container.querySelector(`[data-testid="${testId}"]`) !== null;

  it('opens the step the stage starts on in place of the address, and keeps the search params', async () => {
    const entries = [`${STAGE_PATH}?page=p2`];
    const { router } = mount(entries, deferred<void>());

    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    await vi.waitFor(() => expect(shown('stage-screen')).toBe(true));

    expect(router.state.location.pathname).toBe(`${STAGE_PATH}/steps/${DEFAULT_STEP}`);
    expect(router.state.location.search).toMatchObject({ page: 'p2' });
    // The address that named no step is gone from the history, so Back does not return to it
    expect(entries).toEqual([`${STAGE_PATH}/steps/${DEFAULT_STEP}?page=p2`]);
  });

  it('stays on a stage that has no step bar, and reads nothing for it', async () => {
    const entries = ['/projects/book/stages/page-split'];
    const { router } = mount(entries, deferred<void>());

    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    await vi.waitFor(() => expect(shown('stage-screen')).toBe(true));

    expect(entries).toEqual(['/projects/book/stages/page-split']);
    expect(sdk.processors).not.toHaveBeenCalled();
  });

  it('keeps the address the reader moved to while the recipes were still being read', async () => {
    const recipesHeld = deferred<typeof RECIPES>();
    const viewerHeld = deferred<void>();
    sdk.recipes.mockReset().mockReturnValue(recipesHeld.promise);
    const entries = [STAGE_PATH];
    const { router, runs } = mount(entries, viewerHeld);

    // The reader opens the stage and, before its recipes arrive, clicks the link to the reading mode, whose screen is
    // still loading
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    await vi.waitFor(() => expect(runs).toHaveLength(1));
    await act(async () => {
      router.history.push(VIEWER_PATH);
    });

    // The recipes arrive, and the load that was cancelled by the click reaches its redirect to the step
    recipesHeld.resolve(RECIPES);
    await expect(runs[0]).rejects.toMatchObject({ options: { to: STEP_ROUTE, replace: true } });
    // The reading mode finishes loading
    viewerHeld.resolve();
    await vi.waitFor(() => expect(shown('viewer')).toBe(true));

    expect(router.state.location.pathname).toBe(VIEWER_PATH);
    expect(entries).toEqual([STAGE_PATH, VIEWER_PATH]);
    expect(entries.some((entry) => entry.includes('/steps/'))).toBe(false);
  });
});
