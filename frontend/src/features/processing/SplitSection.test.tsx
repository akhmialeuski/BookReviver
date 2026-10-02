import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { useRunStage } from '@/features/processing/queries';
import { SplitSection } from '@/features/processing/SplitSection';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';
import { ProblemError } from '@/shared/http/problem';

/**
 * The choice of one page or two for a scan, which is kept as an edit of the automatic split, the way back to the
 * automatic decision, and the question that stands before a scan goes back to one page.
 */

const sdk = vi.hoisted(() => ({
  run: vi.fn(),
  jobs: vi.fn(),
  edits: vi.fn(),
  put: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGet: sdk.edits,
  putEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyPut: sdk.put,
  deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyDelete: sdk.remove,
}));

const AUTO = recipe('auto', { stage: 'page-split', steps: [step('split.auto')] });
const WHOLE = recipe('whole', { stage: 'page-split', active: false, steps: [step('split.none')] });
const STATE = processing({ stage: 'page-split', recipes: [AUTO, WHOLE], recipe: AUTO });

/** The edit of the automatic split a reader left on a page. */
function edit(pages: number) {
  return {
    page_id: 'w',
    stage: 'page-split',
    processor_key: 'split.auto',
    kind: 'split',
    geometry: { pages, line: null },
    mask: null,
    edit_hash: 'hash',
    updated_at: '2026-10-02T10:00:00Z',
  };
}

describe('SplitSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** A control elsewhere on the screen that sends the same request to run the stage as the banners and the footer do. */
  function OtherRun(): React.JSX.Element {
    const run = useRunStage('project', 'page-split');
    return (
      <button
        type="button"
        data-testid="other-run"
        onClick={() =>
          run.mutate({
            path: { project_id: 'project', stage: 'page-split' },
            body: { recipe_id: 'auto' },
          })
        }
      />
    );
  }

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
          <OtherRun />
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
  const testid = (id: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const settle = () =>
    act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  const whole = [page('w', { scan_id: 's', slot: 0 })];
  const halves = [page('l', { scan_id: 's', slot: 1 }), page('r', { scan_id: 's', slot: 2 })];

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const mock of [sdk.run, sdk.put, sdk.remove]) {
      mock.mockReset();
      mock.mockResolvedValue({ data: {} });
    }
    sdk.jobs.mockReset();
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    sdk.edits.mockReset();
    sdk.edits.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
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
    await render(whole, 0);
    expect(radio('One page')?.checked).toBe(true);

    await render(halves, 0);
    expect(radio('Two pages')?.checked).toBe(true);
  });

  it('keeps the choice as an edit of the automatic split and recomputes the page, with no other recipe run', async () => {
    await render(whole, 0);

    await act(async () => {
      radio('Two pages')?.click();
    });
    await settle();

    expect(dialog()).toBeNull();
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { page_id: 'w', stage: 'page-split', processor_key: 'split.auto' },
      body: { kind: 'split', geometry: '{"pages":2}' },
    });
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'auto', page_ids: ['w'] });
  });

  it('asks before the scan goes back to one page and sends nothing until the answer', async () => {
    await render(halves, 0);

    await act(async () => {
      radio('One page')?.click();
    });

    expect(dialog()?.textContent).toContain('Go back to one page?');
    expect(dialog()?.textContent).toContain('The right page of this scan is deleted');
    expect(sdk.put).not.toHaveBeenCalled();
    expect(sdk.run).not.toHaveBeenCalled();
  });

  it('sends nothing when the reader keeps two pages', async () => {
    await render(halves, 0);
    await act(async () => {
      radio('One page')?.click();
    });

    await act(async () => {
      const buttons = [...(dialog()?.querySelectorAll('button') ?? [])];
      buttons.find((button) => button.textContent === 'Keep two pages')?.click();
    });

    expect(sdk.put).not.toHaveBeenCalled();
    expect(sdk.run).not.toHaveBeenCalled();
    expect(radio('Two pages')?.checked).toBe(true);
  });

  it('saves one page for the left page and runs it with the confirmation, once it is given', async () => {
    await render(halves, 0);
    await act(async () => {
      radio('One page')?.click();
    });

    await act(async () => {
      document.body.querySelector<HTMLElement>('[data-testid="unsplit-confirm"]')?.click();
    });
    await settle();

    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { page_id: 'l', processor_key: 'split.auto' },
      body: { kind: 'split', geometry: '{"pages":1}' },
    });
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
      recipe_id: 'auto',
      page_ids: ['l'],
      confirm_unsplit: true,
    });
  });

  it('runs nothing when the edit could not be saved, and says why', async () => {
    sdk.put.mockRejectedValue(new ProblemError('The edit was refused.', 422, null, []));
    await render(whole, 0);

    await act(async () => {
      radio('Two pages')?.click();
    });
    await settle();

    expect(sdk.run).not.toHaveBeenCalled();
    expect(container.textContent).toContain('The edit was refused.');
  });

  it('holds the choice while a run sent from another control is on its way, and frees it once the run is on the list', async () => {
    let answer: (value: unknown) => void = () => undefined;
    sdk.run.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    await render(whole, 0);
    expect(radio('Two pages')?.disabled).toBe(false);

    await act(async () => {
      testid('other-run')?.click();
    });
    await settle();
    expect(radio('Two pages')?.disabled).toBe(true);
    await act(async () => {
      radio('Two pages')?.click();
    });
    expect(sdk.put).not.toHaveBeenCalled();

    // The server took the run, and its job is on the list when the request ends
    sdk.jobs.mockResolvedValue({
      data: { items: [{ id: 'j' }], total: 1, page: 1, size: 20, pages: 1 },
    });
    await act(async () => {
      answer({ data: { id: 'j' } });
    });
    await settle();
    expect(radio('Two pages')?.disabled).toBe(true);

    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    await act(async () => {
      await client.invalidateQueries();
    });
    await settle();
    expect(radio('Two pages')?.disabled).toBe(false);
  });

  it('offers the automatic decision only to a scan the reader chose for', async () => {
    await render(whole, 0);
    expect(testid('split-auto')).toBeNull();
    expect(testid('split-automatic')?.textContent).toBe('The automatic split decides.');

    sdk.edits.mockResolvedValue({
      data: { items: [edit(2)], total: 1, page: 1, size: 100, pages: 1 },
    });
    client.clear();
    await render(whole, 0);

    expect(testid('split-chosen')?.textContent).toBe('You chose: Two pages.');
    expect(testid('split-auto')?.textContent).toBe('Auto');
  });

  it('deletes the edit and recomputes the page when Auto is pressed on a scan kept whole', async () => {
    sdk.edits.mockResolvedValue({
      data: { items: [edit(1)], total: 1, page: 1, size: 100, pages: 1 },
    });
    await render(whole, 0);

    await act(async () => {
      testid('split-auto')?.click();
    });
    await settle();

    expect(dialog()).toBeNull();
    expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
      path: { page_id: 'w', stage: 'page-split', processor_key: 'split.auto' },
    });
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'auto', page_ids: ['w'] });
  });

  it('asks before Auto is applied to a cut scan, since the automatic split may keep it whole', async () => {
    sdk.edits.mockResolvedValue({
      data: { items: [edit(2)], total: 1, page: 1, size: 100, pages: 1 },
    });
    await render(halves, 0);

    await act(async () => {
      testid('split-auto')?.click();
    });

    expect(dialog()?.textContent).toContain('Return to the automatic split?');
    expect(sdk.remove).not.toHaveBeenCalled();

    await act(async () => {
      document.body.querySelector<HTMLElement>('[data-testid="unsplit-confirm"]')?.click();
    });
    await settle();

    expect(sdk.remove).toHaveBeenCalledTimes(1);
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
      recipe_id: 'auto',
      page_ids: ['l'],
      confirm_unsplit: true,
    });
  });

  it('changes nothing when the choice already stands', async () => {
    await render(whole, 0);

    await act(async () => {
      radio('One page')?.click();
    });

    expect(dialog()).toBeNull();
    expect(sdk.put).not.toHaveBeenCalled();
    expect(sdk.run).not.toHaveBeenCalled();
  });

  it('draws nothing for a page that is not cut from a scan', async () => {
    await render([page('blank', { scan_id: null })], 0);

    expect(container.querySelector('[data-testid="split-section"]')).toBeNull();
  });

  it('says so, and keeps the choice off, when the stage has no recipe with the automatic split', async () => {
    const manual = processing({ stage: 'page-split', recipes: [WHOLE], recipe: WHOLE });
    await render(whole, 0, manual);

    await act(async () => {
      radio('Two pages')?.click();
    });

    expect(container.textContent).toContain('no recipe with the automatic split');
    expect(sdk.put).not.toHaveBeenCalled();
    expect(sdk.run).not.toHaveBeenCalled();
  });

  it('keeps the choice off while the recipe has changes that are not saved', async () => {
    await render(whole, 0, { ...STATE, dirty: true });

    await act(async () => {
      radio('Two pages')?.click();
    });

    expect(sdk.put).not.toHaveBeenCalled();
    expect(sdk.run).not.toHaveBeenCalled();
  });
});
