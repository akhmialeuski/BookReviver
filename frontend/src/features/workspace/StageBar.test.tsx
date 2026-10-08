import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSchema, StageSummarySchema } from '@/api';
import {
  listStagesApiV1ProjectsProjectIdStagesGetOptions,
  projectApiV1ProjectsProjectIdGetOptions,
} from '@/api/@tanstack/react-query.gen';
import { projectWith } from '@/features/about/fixtures';
import { jobsOptions } from '@/features/workspace/queries';
import { StageBar } from '@/features/workspace/StageBar';
import { NARROW_QUERY } from '@/shared/hooks/useMediaQuery';

/**
 * The bar of the stages in a narrow window: the open stage whole, the others in a menu.
 *
 * The queries the bar reads are filled in beforehand and never go stale, so it draws from them without a server.
 */

const PROJECT_ID = 'project-1';

const IMPORT_SUMMARY: StageSummarySchema = {
  stage: 'import',
  available: true,
  manual: true,
  pages: 0,
  fresh: 0,
  stale: 0,
  failed: 0,
  not_run: 0,
  review: 0,
  check: 0,
  partial: 0,
  recipes: [],
  stopped: [],
};

describe('StageBar, the Import stage', () => {
  let container: HTMLDivElement;
  let root: Root;

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

  async function render(
    counts: { files: number; scans: number },
    active: JobSchema[],
    path = '/',
    summaries: StageSummarySchema[] = [IMPORT_SUMMARY],
  ) {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    client.setQueryData(
      projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: PROJECT_ID } }).queryKey,
      {
        ...projectWith(),
        source_count: counts.files,
        scan_count: counts.scans,
        page_count: counts.scans,
      },
    );
    client.setQueryData(
      listStagesApiV1ProjectsProjectIdStagesGetOptions({ path: { project_id: PROJECT_ID } })
        .queryKey,
      { items: summaries, total: summaries.length, page: 1, size: 100, pages: 1 },
    );
    client.setQueryData(jobsOptions(PROJECT_ID, true).queryKey, active);

    const rootRoute = createRootRoute({
      component: () => (
        <QueryClientProvider client={client}>
          <StageBar projectId={PROJECT_ID} />
        </QueryClientProvider>
      ),
    });
    const children = ['/projects/$projectId/stages/$stage', '/projects/$projectId/about'].map(
      (path) => createRoute({ getParentRoute: () => rootRoute, path }),
    );
    const router = createRouter({
      routeTree: rootRoute.addChildren(children),
      history: createMemoryHistory({ initialEntries: [path] }),
    });
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
  }

  describe('in a narrow window', () => {
    const IMPORT_PATH = `/projects/${PROJECT_ID}/stages/import`;

    beforeEach(() => {
      // Only the query of the narrow layout matches, as in a window below the lg breakpoint
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: query === NARROW_QUERY,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }));
    });

    it('shows the open stage whole and puts the others in a menu', async () => {
      await render({ files: 3, scans: 122 }, [], IMPORT_PATH);

      const current = container.querySelector('[data-testid="stage-import"]');
      expect(current?.getAttribute('aria-current')).toBe('page');
      expect(current?.textContent).toContain('Import');
      expect(container.querySelector('[data-testid="stage-page-split"]')).toBeNull();
      expect(container.querySelector('[data-testid="stage-menu"]')).not.toBeNull();
    });

    it('opens the menu again while the list of the last pick is still fading out', async () => {
      // jsdom runs no animation, so the closed list is given one, which Radix waits out before it unmounts the list
      const computedStyle = window.getComputedStyle.bind(window);
      const fade = vi.spyOn(window, 'getComputedStyle').mockImplementation(
        (element, pseudo) =>
          new Proxy(computedStyle(element, pseudo), {
            get: (style, key) =>
              key === 'animationName'
                ? element.getAttribute('data-state') === 'closed'
                  ? 'fade-out'
                  : 'none'
                : Reflect.get(style, key, style),
          }),
      );
      await render({ files: 3, scans: 122 }, [], IMPORT_PATH);
      const trigger = container.querySelector('[data-testid="stage-menu"]');
      const list = (): Element | null => document.querySelector('[data-testid="stage-menu-list"]');
      const press = async (target: Element | null): Promise<void> => {
        await act(async () => {
          target?.dispatchEvent(
            new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 }),
          );
          await new Promise((resolve) => setTimeout(resolve));
        });
      };

      await press(trigger);
      expect(list()?.getAttribute('data-state')).toBe('open');
      await act(async () => {
        list()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });
      expect(list()?.getAttribute('data-state')).toBe('closed');

      await press(trigger);

      expect(list()?.getAttribute('data-state')).toBe('open');
      fade.mockRestore();
    });

    it('keeps the ten stages in a row in a wide window', async () => {
      vi.stubGlobal('matchMedia', undefined);
      await render({ files: 3, scans: 122 }, [], IMPORT_PATH);

      expect(container.querySelector('[data-testid="stage-page-split"]')).not.toBeNull();
      expect(container.querySelector('[data-testid="stage-menu"]')).toBeNull();
    });

    it('shows no counts under the stages, whatever the pages of the book are doing in them', async () => {
      vi.stubGlobal('matchMedia', undefined);
      const geometry: StageSummarySchema = {
        ...IMPORT_SUMMARY,
        stage: 'geometry',
        manual: false,
        pages: 120,
        fresh: 100,
        stale: 15,
        failed: 5,
        check: 7,
        partial: 3,
      };
      await render({ files: 3, scans: 122 }, [], IMPORT_PATH, [IMPORT_SUMMARY, geometry]);

      const bar = container.querySelector('[data-testid="stage-bar"]');
      expect(bar?.textContent).toContain('Geometry');
      expect(bar?.textContent).not.toMatch(
        /\d{3}|\bfiles?\b|\bscans?\b|\bpages?\b|\bout of date\b|\bfailed\b/i,
      );
    });
  });
});
