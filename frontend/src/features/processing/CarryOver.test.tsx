import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CarryOver } from '@/features/processing/CarryOver';

/**
 * The menu that carries a setting of the open page over to other pages: what each choice sends, the line that says what
 * it did with the pages it skipped, and the undo that takes the whole carry-over back.
 */

const sdk = vi.hoisted(() => ({ carry: vi.fn(), shape: vi.fn(), undo: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  carryOverSettingApiV1ProjectsProjectIdPagesPageIdSettingsStageStepIdNameCarryOverPost: sdk.carry,
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPost: sdk.shape,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
}));

const CARRIED = {
  batch_id: 'batch',
  changes: [
    { id: 'change-1', page_id: 'next-1' },
    { id: 'change-2', page_id: 'next-2' },
  ],
  skipped: ['own'],
};

describe('CarryOver', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(
    selected: ReadonlySet<string> = new Set(['page', 'x', 'y']),
    overwrite = false,
    name: string | null = 'max_angle',
  ): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <CarryOver
            processing={{ projectId: 'project', stage: 'geometry' }}
            pageId="page"
            stepId="step"
            name={name ?? undefined}
            title={name === null ? 'the shape' : 'Largest slant'}
            selected={selected}
            overwrite={overwrite}
          >
            <span data-testid="beside">beside</span>
          </CarryOver>
        </QueryClientProvider>,
      ),
    );
  }

  /** Open the menu the way a keyboard does, which Radix answers in jsdom as it does in a browser. */
  async function openMenu(): Promise<void> {
    const trigger = container.querySelector<HTMLElement>('[data-testid="carry-menu"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  async function choose(id: string): Promise<void> {
    await openMenu();
    await act(async () => {
      document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.carry.mockReset();
    sdk.shape.mockReset();
    sdk.undo.mockReset();
    sdk.carry.mockResolvedValue({ data: CARRIED });
    sdk.shape.mockResolvedValue({ data: CARRIED });
    sdk.undo.mockResolvedValue({ data: { changes: [] } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.querySelectorAll('[role="menu"]').forEach((node) => {
      node.remove();
    });
    client.clear();
    vi.unstubAllGlobals();
  });

  it('names the field it carries on the button and keeps what stands beside it', () => {
    render();

    const button = container.querySelector('[data-testid="carry-menu"]');
    expect(button?.getAttribute('aria-label')).toBe('Carry Largest slant over to other pages');
    expect(container.querySelector('[data-testid="beside"]')).not.toBeNull();
  });

  it('carries the value to the following pages of the open page, by the field and the step', async () => {
    render();

    await choose('carry-following');

    expect(sdk.carry).toHaveBeenCalledTimes(1);
    expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
      path: {
        project_id: 'project',
        page_id: 'page',
        stage: 'geometry',
        step_id: 'step',
        name: 'max_angle',
      },
      body: { scope: 'following', overwrite: false },
    });
    expect(sdk.carry.mock.calls[0]?.[0].body).not.toHaveProperty('page_ids');
  });

  it('carries the value to every page of the kind of the open page', async () => {
    render();

    await choose('carry-kind');

    expect(sdk.carry.mock.calls[0]?.[0].body).toEqual({ scope: 'kind', overwrite: false });
  });

  it('carries the value to the selected pages without the open page, and counts them in the menu', async () => {
    render();
    await openMenu();
    expect(document.body.querySelector('[data-testid="carry-selected"]')?.textContent).toBe(
      'To the selected pages · 2',
    );

    await act(async () => {
      document.body.querySelector<HTMLElement>('[data-testid="carry-selected"]')?.click();
    });

    expect(sdk.carry.mock.calls[0]?.[0].body).toEqual({
      scope: 'selected',
      overwrite: false,
      page_ids: ['x', 'y'],
    });
  });

  it('offers no carry-over to the selected pages when only the open page is selected', async () => {
    render(new Set(['page']));
    await openMenu();

    const item = document.body.querySelector('[data-testid="carry-selected"]');
    expect(item?.getAttribute('aria-disabled')).toBe('true');
  });

  it('writes over the pages that have a value of their own when asked to', async () => {
    render(new Set(), true);

    await choose('carry-following');

    expect(sdk.carry.mock.calls[0]?.[0].body).toEqual({ scope: 'following', overwrite: true });
  });

  it('says how many pages took the value and how many were skipped for a value of their own', async () => {
    render();

    await choose('carry-following');

    expect(container.querySelector('[data-testid="carry-result"]')?.textContent).toContain(
      'Carried over to 2 pages, 1 page was skipped for a value of their own.',
    );
  });

  it('says no page was skipped when none was', async () => {
    sdk.carry.mockResolvedValue({ data: { ...CARRIED, skipped: [] } });
    render();

    await choose('carry-following');

    expect(container.querySelector('[data-testid="carry-result"]')?.textContent).toContain(
      'Carried over to 2 pages.',
    );
  });

  it('takes the carry-over back with one undo that names a change of the batch', async () => {
    render();
    await choose('carry-following');

    await act(async () => {
      container.querySelector<HTMLElement>('[data-testid="carry-undo"]')?.click();
    });

    expect(sdk.undo).toHaveBeenCalledTimes(1);
    expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'next-1', stage: 'geometry', step_id: 'step' },
      body: { change_id: 'change-1' },
    });
    expect(container.querySelector('[data-testid="carry-result"]')).toBeNull();
  });

  it('offers no undo when no page took the value', async () => {
    sdk.carry.mockResolvedValue({ data: { ...CARRIED, changes: [], skipped: ['own'] } });
    render();

    await choose('carry-following');

    expect(container.querySelector('[data-testid="carry-result"]')?.textContent).toContain(
      'Carried over to 0 pages',
    );
    expect(container.querySelector('[data-testid="carry-undo"]')).toBeNull();
  });

  describe('with no field named', () => {
    it('carries the shape set by hand through the route of the edits, naming no field', async () => {
      render(new Set(['page']), true, null);

      await choose('carry-kind');

      expect(sdk.carry).not.toHaveBeenCalled();
      expect(sdk.shape).toHaveBeenCalledTimes(1);
      expect(sdk.shape.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', stage: 'geometry', step_id: 'step' },
        body: { scope: 'kind', overwrite: true },
      });
      expect(sdk.shape.mock.calls[0]?.[0].path).not.toHaveProperty('name');
    });

    it('names the shape on the button and takes the whole batch back with one undo', async () => {
      render(new Set(['page']), false, null);
      expect(
        container.querySelector('[data-testid="carry-menu"]')?.getAttribute('aria-label'),
      ).toBe('Carry the shape over to other pages');

      await choose('carry-following');
      await act(async () => {
        container.querySelector<HTMLElement>('[data-testid="carry-undo"]')?.click();
      });

      expect(sdk.undo).toHaveBeenCalledTimes(1);
      expect(sdk.undo.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'next-1', step_id: 'step' },
        body: { change_id: 'change-1' },
      });
    });
  });
});
