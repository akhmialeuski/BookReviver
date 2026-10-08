import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe } from '@/features/processing/fixtures';
import { row } from '@/features/workspace/fixtures';
import { ResetSteps } from '@/features/workspace/ResetSteps';
import { ProblemError } from '@/shared/http/problem';

/**
 * The button that puts the default steps back and its confirmation, which names how many pages the reset makes out of
 * date, says when unsaved changes of the draft go with it, and sends nothing until it is confirmed.
 */

const sdk = vi.hoisted(() => ({ reset: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  resetRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdResetPost: sdk.reset,
}));

describe('ResetSteps', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const discard = vi.fn();

  function render(overrides: Parameters<typeof processing>[0] = {}, fresh = 2): void {
    const rows = Array.from({ length: fresh }, (_, index) =>
      row(`p${index}`, { recipe_id: 'r1', status: 'fresh' }),
    );
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ResetSteps processing={processing({ discard, ...overrides })} rows={rows} />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  async function open(): Promise<void> {
    await act(async () => {
      byId('steps-reset')?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.reset.mockReset();
    sdk.reset.mockResolvedValue({ data: recipe('r1') });
    discard.mockReset();
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

  it('sends nothing until the reset is confirmed, and names the pages that go out of date', async () => {
    render();
    await open();

    expect(byId('steps-reset-pages')?.textContent).toContain('2 pages will be out of date.');
    expect(sdk.reset).not.toHaveBeenCalled();
  });

  it('says so when no page is made out of date', async () => {
    render({}, 0);
    await open();

    expect(byId('steps-reset-pages')?.textContent).toContain('No page is made out of date.');
  });

  it('warns that the changes of the draft that are not saved are dropped', async () => {
    render();
    await open();
    expect(byId('steps-reset-unsaved')).toBeNull();

    render({ dirty: true });
    expect(byId('steps-reset-unsaved')?.textContent).toContain('not saved');
  });

  it('resets the recipe that is shown, then drops the draft and closes the confirmation', async () => {
    render();
    await open();
    await act(async () => {
      byId('steps-reset-confirm')?.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(sdk.reset).toHaveBeenCalledTimes(1);
    expect(sdk.reset.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', recipe_id: 'r1' },
    });
    expect(discard).toHaveBeenCalledOnce();
    expect(byId('steps-reset-dialog')).toBeNull();
  });

  it('keeps the confirmation open and shows the answer when the server refuses', async () => {
    sdk.reset.mockRejectedValue(
      new ProblemError('Another job of this book is running.', 409, null, []),
    );
    render();
    await open();
    await act(async () => {
      byId('steps-reset-confirm')?.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(discard).not.toHaveBeenCalled();
    expect(byId('steps-reset-dialog')).not.toBeNull();
    expect(byId('steps-reset-dialog')?.textContent).toContain(
      'Another job of this book is running.',
    );
  });
});
