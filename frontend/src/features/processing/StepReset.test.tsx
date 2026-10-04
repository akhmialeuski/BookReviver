import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StepReset } from '@/features/processing/StepReset';
import { ProblemError } from '@/shared/http/problem';

/**
 * The menu that resets a step to its defaults: what each of the four choices sends, the question a reset that reaches
 * other pages asks first with the number of pages, the line that says what the reset did, and the undo that takes the
 * whole batch back.
 */

const sdk = vi.hoisted(() => ({ reset: vi.fn(), impact: vi.fn(), undo: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  resetStepsApiV1ProjectsProjectIdStagesStageResetPost: sdk.reset,
  resetImpactApiV1ProjectsProjectIdStagesStageResetImpactPost: sdk.impact,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
}));

const DONE = {
  batch_id: 'batch',
  changes: [
    { id: 'change-1', page_id: 'page', step_id: 'step' },
    { id: 'change-2', page_id: 'page', step_id: 'step' },
    { id: 'change-3', page_id: 'other', step_id: 'step' },
  ],
};

function impactOf(scope: string, affected: number): { data: Record<string, unknown> } {
  return { data: { scope, hand_pages: 1, settings_pages: 2, affected } };
}

describe('StepReset', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(pageId: string | null = 'page'): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <StepReset
            processing={{ projectId: 'project', stage: 'geometry' }}
            pageId={pageId ?? undefined}
            stepId="step"
            title="Deskew"
          />
        </QueryClientProvider>,
      ),
    );
  }

  /** Open the menu the way a keyboard does, which Radix answers in jsdom as it does in a browser. */
  async function openMenu(): Promise<void> {
    const trigger = container.querySelector<HTMLElement>('[data-testid="reset-menu"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  async function choose(scope: string): Promise<void> {
    await openMenu();
    await act(async () => {
      document.body.querySelector<HTMLElement>(`[data-testid="reset-${scope}"]`)?.click();
    });
  }

  async function press(id: string): Promise<void> {
    await act(async () => {
      document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
    });
  }

  const find = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.reset.mockReset();
    sdk.impact.mockReset();
    sdk.undo.mockReset();
    sdk.reset.mockResolvedValue({ data: DONE });
    sdk.impact.mockResolvedValue(impactOf('step', 3));
    sdk.undo.mockResolvedValue({ data: { changes: [] } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.querySelectorAll('[role="menu"], [role="dialog"]').forEach((node) => {
      node.remove();
    });
    client.clear();
    vi.unstubAllGlobals();
  });

  it('names the step on the menu', () => {
    render();

    expect(find('reset-menu')?.getAttribute('aria-label')).toBe('Reset Deskew to the defaults');
  });

  it('offers the four scopes in words, and the two of one page only when a page is open', async () => {
    render(null);
    await openMenu();

    expect(
      ['page-step', 'page', 'step', 'stage'].map((scope) => find(`reset-${scope}`)?.textContent),
    ).toEqual([
      'This step on this page',
      'Every step on this page',
      'This step on every page',
      'Every step of the stage on every page',
    ]);
    expect(find('reset-page-step')?.getAttribute('aria-disabled')).toBe('true');
    expect(find('reset-page')?.getAttribute('aria-disabled')).toBe('true');
    expect(find('reset-step')?.getAttribute('aria-disabled')).toBeNull();
  });

  it('resets the step on the open page at once, naming the page and the step', async () => {
    render();

    await choose('page-step');

    expect(sdk.impact).not.toHaveBeenCalled();
    expect(sdk.reset.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { scope: 'page-step', page_id: 'page', step_id: 'step' },
    });
    expect(sdk.reset.mock.calls[0]?.[0].body).not.toHaveProperty('confirm');
  });

  it('resets every step on the open page at once, naming the page and no step', async () => {
    render();

    await choose('page');

    expect(sdk.impact).not.toHaveBeenCalled();
    expect(sdk.reset.mock.calls[0]?.[0].body).toEqual({ scope: 'page', page_id: 'page' });
  });

  it('counts the pages of the step on every page first, and warns with the number', async () => {
    render();

    await choose('step');

    expect(sdk.impact.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { scope: 'step', step_id: 'step' },
    });
    expect(sdk.reset).not.toHaveBeenCalled();
    expect(find('reset-pages')?.textContent).toContain('This step on every page.');
    expect(find('reset-pages')?.textContent).toContain('3 pages lose work of their own');
    expect(find('reset-pages')?.textContent).toContain('1 page has a hand edit');
    expect(find('reset-pages')?.textContent).toContain('2 pages change a setting');
    expect(find('reset-dialog')?.textContent).toContain('one undo gives it back');
  });

  it('counts the pages of the whole stage first, naming neither the page nor the step', async () => {
    sdk.impact.mockResolvedValue(impactOf('stage', 5));
    render();

    await choose('stage');

    expect(sdk.impact.mock.calls[0]?.[0].body).toEqual({ scope: 'stage' });
    expect(find('reset-pages')?.textContent).toContain('Every step of the stage on every page.');
    expect(find('reset-pages')?.textContent).toContain('5 pages lose work of their own');
  });

  it('sends the reset with the confirmation once the warning is accepted', async () => {
    render();
    await choose('step');

    await press('reset-confirm');

    expect(sdk.reset.mock.calls[0]?.[0].body).toEqual({
      scope: 'step',
      step_id: 'step',
      confirm: true,
    });
    expect(find('reset-dialog')).toBeNull();
  });

  it('sends nothing when the warning is declined', async () => {
    render();
    await choose('stage');

    await act(async () => {
      const buttons = [...document.body.querySelectorAll<HTMLElement>('[role="dialog"] button')];
      buttons.find((button) => button.textContent === 'Cancel')?.click();
    });

    expect(sdk.reset).not.toHaveBeenCalled();
    expect(find('reset-dialog')).toBeNull();
  });

  it('resets without a question when no page has anything to lose', async () => {
    sdk.impact.mockResolvedValue(impactOf('step', 0));
    render();

    await choose('step');

    expect(find('reset-dialog')).toBeNull();
    expect(sdk.reset.mock.calls[0]?.[0].body).toEqual({ scope: 'step', step_id: 'step' });
  });

  it('says how many layers of how many pages the reset took', async () => {
    render();

    await choose('page-step');

    expect(find('reset-result')?.textContent).toContain('Reset 3 layers on 2 pages.');
  });

  it('says there was nothing to reset, and offers no undo, when no change was written', async () => {
    sdk.reset.mockResolvedValue({ data: { batch_id: 'batch', changes: [] } });
    render();

    await choose('page-step');

    expect(find('reset-result')?.textContent).toContain('There was nothing to reset.');
    expect(find('reset-undo')).toBeNull();
  });

  it('takes the reset back with one undo that names a change of the batch', async () => {
    render();
    await choose('page-step');

    await press('reset-undo');

    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: 'step' },
      body: { change_id: 'change-1' },
    });
    expect(find('reset-result')).toBeNull();
  });

  it('shows what the server refused', async () => {
    sdk.reset.mockRejectedValue(new ProblemError('The reset needs a page.', 409, null, []));
    render();

    await choose('page-step');

    expect(container.textContent).toContain('The reset needs a page.');
  });
});
