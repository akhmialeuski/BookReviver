import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Route as stageRoute } from '@/routes/_authenticated/projects/$projectId/stages.$stage';

/**
 * The route of a stage as it hands the screen its moves between steps: a stage with a bar opens on a step by an effect of
 * the screen, so the functions it is given must not change while the address stays where it is.
 *
 * The screen is a stand-in that makes the move in an effect, as the real one does, and the router is the real one over a
 * memory history, with the moves it is asked for counted and not made.
 */

vi.mock('@/features/workspace/StageScreen', async () => {
  const { useEffect } = await import('react');
  return {
    StageScreen: ({
      stepId,
      onDefaultStep,
    }: {
      stepId?: string;
      onDefaultStep: (stepId: string) => void;
    }) => {
      useEffect(() => {
        if (stepId === undefined) {
          onDefaultStep('step-1');
        }
      }, [stepId, onDefaultStep]);
      return <p data-testid="stage-screen" />;
    },
  };
});
vi.mock('@/features/import/ImportScreen', () => ({ ImportScreen: () => null }));
vi.mock('@/features/order/OrderScreen', () => ({ OrderScreen: () => null }));
vi.mock('@/features/place/PlaceWriterContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/place/PlaceWriterContext')>()),
  useBookPlaceWriter: () => null,
}));

const STAGE_PATH = '/projects/book/stages/geometry';

/** The longest the split chunk of the route takes to import, which pulls in the whole workspace. */
const CHUNK_IMPORT_TIMEOUT_MS = 60_000;

describe('the route of a stage', () => {
  let container: HTMLDivElement;
  let root: Root;

  // The build splits the component of a route into a chunk of its own, which the router imports before it draws, so
  // the chunk is imported once here and the test counts only the moves
  beforeAll(async () => {
    await stageRoute.options.component?.preload?.();
  }, CHUNK_IMPORT_TIMEOUT_MS);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('scrollTo', vi.fn());
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('moves to the step the stage opens on once, however often the screen is drawn again with the address unchanged', async () => {
    // The route is the one of the application, hung under the parents that give it its identifier
    const rootRoute = createRootRoute();
    const layout = createRoute({ getParentRoute: () => rootRoute, id: '/_authenticated' });
    const project = createRoute({ getParentRoute: () => layout, path: '/projects/$projectId' });
    const stage = createRoute({
      getParentRoute: () => project,
      path: '/stages/$stage',
      validateSearch: stageRoute.options.validateSearch,
      component: stageRoute.options.component,
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([layout.addChildren([project.addChildren([stage])])]),
      history: createMemoryHistory({ initialEntries: [STAGE_PATH] }),
    });
    // The router loads the route itself once it is mounted, so the moves are counted from the first drawing, and none
    // is made, so the address keeps naming no step
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(undefined);
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="stage-screen"]')).not.toBeNull(),
    );
    expect(navigate).toHaveBeenCalledTimes(1);

    // Moving on the page draws the route again with no step in the address
    for (const page of ['p2', 'p3']) {
      await act(async () => {
        router.history.push(`${STAGE_PATH}?page=${page}`);
      });
      await vi.waitFor(() => expect(router.state.location.search).toMatchObject({ page }));
    }

    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate.mock.calls[0]?.[0]).toMatchObject({
      to: '/projects/$projectId/stages/$stage/steps/$stepId',
      replace: true,
    });
  });
});
