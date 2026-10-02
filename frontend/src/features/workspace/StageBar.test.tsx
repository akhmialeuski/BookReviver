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
import { job } from '@/features/import/fixtures';
import { jobsOptions } from '@/features/workspace/queries';
import { StageBar } from '@/features/workspace/StageBar';

/**
 * What the Import stage says under its name in the bar: the counts of files and scans, the progress of an import that
 * runs in their place, and that the book has no file.
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
  active_recipe_id: null,
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

  async function render(counts: { files: number; scans: number }, active: JobSchema[]) {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    client.setQueryData(
      projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: PROJECT_ID } }).queryKey,
      {
        ...projectWith(),
        source_count: counts.files,
        scan_count: counts.scans,
      },
    );
    client.setQueryData(
      listStagesApiV1ProjectsProjectIdStagesGetOptions({ path: { project_id: PROJECT_ID } })
        .queryKey,
      { items: [IMPORT_SUMMARY], total: 1, page: 1, size: 100, pages: 1 },
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
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
  }

  const note = (): string =>
    container.querySelector('[data-testid="stage-import"]')?.textContent ?? '';

  it('counts the files and the scans of a book that has them', async () => {
    await render({ files: 3, scans: 122 }, []);

    expect(note()).toContain('3 files · 122 scans');
  });

  it('says a book without files has none yet', async () => {
    await render({ files: 0, scans: 0 }, []);

    expect(note()).toContain('No files yet');
  });

  it('shows how far the running import has come in place of the counts', async () => {
    await render({ files: 3, scans: 122 }, [job('j-1')]);

    expect(note()).toContain('Importing 12 of 18');
    expect(note()).not.toContain('122 scans');
  });

  it('ignores a running job that is not an import', async () => {
    await render({ files: 3, scans: 122 }, [job('j-1', { kind: 'run-stage' })]);

    expect(note()).toContain('3 files · 122 scans');
  });
});
