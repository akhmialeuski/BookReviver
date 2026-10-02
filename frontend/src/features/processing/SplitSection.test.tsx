import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { SplitSection } from '@/features/processing/SplitSection';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * The choice of one page or two for a scan, and the question that stands before a scan goes back to one page.
 */

const sdk = vi.hoisted(() => ({ run: vi.fn(), jobs: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

const WHOLE = recipe('whole', { stage: 'page-split', steps: [step('split.none')] });
const SPREAD = recipe('spread', {
  stage: 'page-split',
  active: false,
  steps: [step('split.spread')],
});
const STATE = processing({ stage: 'page-split', recipes: [WHOLE, SPREAD], recipe: WHOLE });

describe('SplitSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(pages: ReturnType<typeof page>[], open: number, state = STATE) {
    const items = joinRows(
      pages,
      pages.map((entry) => row(entry.id)),
    );
    const current = items[open];
    if (current === undefined) {
      throw new Error(`The test has no page ${open}`);
    }
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <SplitSection processing={state} items={items} current={current} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const radio = (name: string): HTMLInputElement | undefined =>
    [...container.querySelectorAll<HTMLInputElement>('input[type="radio"]')].find(
      (input) => input.closest('label')?.textContent === name,
    );
  const dialog = (): HTMLElement | null => document.body.querySelector('[role="dialog"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.run.mockReset();
    sdk.run.mockResolvedValue({ data: { id: 'job' } });
    sdk.jobs.mockReset();
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    dialog()?.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  it('shows a scan kept whole as one page and a scan cut as two', async () => {
    await render([page('w', { scan_id: 's', slot: 0 })], 0);
    expect(radio('One page')?.checked).toBe(true);

    await render([page('l', { scan_id: 's', slot: 1 }), page('r', { scan_id: 's', slot: 2 })], 0);
    expect(radio('Two pages')?.checked).toBe(true);
  });

  it('cuts the scan at once when two pages are chosen, with the recipe that cuts', async () => {
    await render([page('w', { scan_id: 's', slot: 0 })], 0);

    await act(async () => {
      radio('Two pages')?.click();
    });

    expect(dialog()).toBeNull();
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'spread', page_ids: ['w'] });
  });

  it('asks before the scan goes back to one page and sends nothing until the answer', async () => {
    await render([page('l', { scan_id: 's', slot: 1 }), page('r', { scan_id: 's', slot: 2 })], 0);

    await act(async () => {
      radio('One page')?.click();
    });

    expect(dialog()?.textContent).toContain('Go back to one page?');
    expect(dialog()?.textContent).toContain('The right page of this scan is deleted');
    expect(sdk.run).not.toHaveBeenCalled();
  });

  it('sends nothing when the reader keeps two pages', async () => {
    await render([page('l', { scan_id: 's', slot: 1 }), page('r', { scan_id: 's', slot: 2 })], 0);
    await act(async () => {
      radio('One page')?.click();
    });

    await act(async () => {
      const buttons = [...(dialog()?.querySelectorAll('button') ?? [])];
      buttons.find((button) => button.textContent === 'Keep two pages')?.click();
    });

    expect(sdk.run).not.toHaveBeenCalled();
    expect(radio('Two pages')?.checked).toBe(true);
  });

  it('sends the run for both pages of the scan, with the confirmation, once it is given', async () => {
    await render([page('l', { scan_id: 's', slot: 1 }), page('r', { scan_id: 's', slot: 2 })], 0);
    await act(async () => {
      radio('One page')?.click();
    });

    await act(async () => {
      document.body.querySelector<HTMLElement>('[data-testid="unsplit-confirm"]')?.click();
    });

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
      recipe_id: 'whole',
      page_ids: ['l', 'r'],
      confirm_unsplit: true,
    });
  });

  it('changes nothing when the choice already stands', async () => {
    await render([page('w', { scan_id: 's', slot: 0 })], 0);

    await act(async () => {
      radio('One page')?.click();
    });

    expect(dialog()).toBeNull();
    expect(sdk.run).not.toHaveBeenCalled();
  });

  it('draws nothing for a page that is not cut from a scan', async () => {
    await render([page('blank', { scan_id: null })], 0);

    expect(container.querySelector('[data-testid="split-section"]')).toBeNull();
  });

  it('keeps the choice off while the recipe has changes that are not saved', async () => {
    await render([page('w', { scan_id: 's', slot: 0 })], 0, { ...STATE, dirty: true });

    await act(async () => {
      radio('Two pages')?.click();
    });

    expect(sdk.run).not.toHaveBeenCalled();
  });
});
