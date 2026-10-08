import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, scan, step } from '@/features/processing/fixtures';
import { StageBanners } from '@/features/processing/StageBanners';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * The banners above the canvas: the one for results that are out of date, which offers a run on exactly those pages, and
 * the one of the Split stage for scans wider than tall, which offers one run that cuts all that are still whole.
 */

const sdk = vi.hoisted(() => ({ run: vi.fn(), jobs: vi.fn(), scans: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listScansApiV1ProjectsProjectIdScansGet: sdk.scans,
}));

const EMPTY = { items: [], total: 0, page: 1, size: 20, pages: 1 };

describe('StageBanners', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(state: ReturnType<typeof processing>, items: ReturnType<typeof joinRows>) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <StageBanners processing={state} items={items} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const banner = (id: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.run.mockReset();
    sdk.run.mockResolvedValue({ data: { id: 'job' } });
    sdk.jobs.mockReset();
    sdk.jobs.mockResolvedValue({ data: EMPTY });
    sdk.scans.mockReset();
    sdk.scans.mockResolvedValue({ data: EMPTY });
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

  describe('the banner of results out of date', () => {
    const items = joinRows(
      [page('a'), page('b'), page('c'), page('hole', { origin: 'placeholder', images: null })],
      [
        row('a', { status: 'fresh' }),
        row('b', { status: 'stale' }),
        row('c', { status: 'stale' }),
        row('hole', { status: 'stale' }),
      ],
    );

    it('names the stage before and what this stage did', async () => {
      await render(processing(), items);

      expect(banner('stale-banner')?.textContent).toContain(
        'Order changed after these pages were straightened',
      );
      expect(banner('stale-banner-run')).toBeNull();
    });

    it('is not there when no page is out of date', async () => {
      await render(processing(), joinRows([page('a')], [row('a')]));

      expect(banner('stale-banner')).toBeNull();
    });
  });

  describe('the banner of the Split stage', () => {
    const cutter = recipe('spread', {
      stage: 'page-split',
      kind: 'bw-picture',
      steps: [step('split.spread')],
    });
    const whole = recipe('whole', { stage: 'page-split', steps: [step('split.none')] });
    const state = processing({ stage: 'page-split', recipes: [whole, cutter], recipe: whole });
    const items = joinRows(
      [
        page('a1', { scan_id: 'a', slot: 1 }),
        page('a2', { scan_id: 'a', slot: 2 }),
        page('b', { scan_id: 'b' }),
        page('c', { scan_id: 'c' }),
        page('t', { scan_id: 't' }),
      ],
      [],
    );

    beforeEach(() => {
      sdk.scans.mockResolvedValue({
        data: {
          items: [
            scan('a', 2000, 1400),
            scan('b', 2000, 1400),
            scan('c', 2000, 1400),
            scan('t', 1000, 1500),
          ],
          total: 4,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
    });

    it('says how many scans are wide and how many are cut already', async () => {
      await render(state, items);

      expect(banner('split-banner')?.textContent).toContain(
        '3 scans are wider than tall and look like open books. 1 is split already; cut the other 2 the same way',
      );
      expect(banner('split-banner-cut')?.textContent).toBe('Split the 2 scans');
    });

    it('cuts the scans still whole in one run', async () => {
      await render(state, items);

      await act(async () => {
        banner('split-banner-cut')?.click();
      });

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });
    });

    it('goes away for now when the reader says not now', async () => {
      await render(state, items);

      await act(async () => {
        const buttons = [...container.querySelectorAll('button')];
        buttons.find((button) => button.textContent === 'Not now')?.click();
      });

      expect(banner('split-banner')).toBeNull();
    });

    it('is not there once every wide scan is cut', async () => {
      const cut = joinRows(
        [page('a1', { scan_id: 'a', slot: 1 }), page('a2', { scan_id: 'a', slot: 2 })],
        [],
      );
      sdk.scans.mockResolvedValue({
        data: { items: [scan('a', 2000, 1400)], total: 1, page: 1, size: 100, pages: 1 },
      });

      await render(state, cut);

      expect(banner('split-banner')).toBeNull();
    });

    it('is not there on a stage other than Split', async () => {
      await render(processing(), items);

      expect(banner('split-banner')).toBeNull();
    });
  });
});
