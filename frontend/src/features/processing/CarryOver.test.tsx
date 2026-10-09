import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CarryOverSchema } from '@/api';
import { CarryOver } from '@/features/processing/CarryOver';

/**
 * The menu that carries the shape the open page has set by hand over to other pages: what each choice sends, the line
 * that says what it did with the pages it skipped, and the undo that takes the whole carry-over back.
 */

const sdk = vi.hoisted(() => ({ carry: vi.fn(), undo: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPost: sdk.carry,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPost: sdk.undo,
}));

// The component reads only the identifiers of the changes, so the other fields of a change are left out
const CARRIED = {
  batch_id: 'batch',
  changes: [
    { id: 'change-1', page_id: 'next-1' },
    { id: 'change-2', page_id: 'next-2' },
  ],
  skipped: ['own'],
} as unknown as CarryOverSchema;

describe('CarryOver', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** Holds the result as the workspace of the step does, which keeps it above the menu. */
  function Host({
    selected,
    overwrite,
    kept,
    shown,
  }: {
    selected: ReadonlySet<string>;
    overwrite: boolean;
    /** The result the workspace already holds when the menu is drawn. */
    kept: CarryOverSchema | null;
    /** Whether the menu is drawn, which it is not once the reader moved to a page without a shape set by hand. */
    shown: boolean;
  }): React.JSX.Element {
    const [result, setResult] = useState<CarryOverSchema | null>(kept);
    return (
      <>
        <output data-testid="kept">{result?.batch_id ?? ''}</output>
        {shown ? (
          <CarryOver
            processing={{ projectId: 'project', stage: 'geometry' }}
            pageId="page"
            stepId="step"
            title="the shape"
            selected={selected}
            overwrite={overwrite}
            result={result}
            onResult={setResult}
          />
        ) : null}
      </>
    );
  }

  function render(
    selected: ReadonlySet<string> = new Set(['page', 'x', 'y']),
    overwrite = false,
    kept: CarryOverSchema | null = null,
    shown = true,
  ): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Host selected={selected} overwrite={overwrite} kept={kept} shown={shown} />
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
    sdk.undo.mockReset();
    sdk.carry.mockResolvedValue({ data: CARRIED });
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

  it('names what it carries on the button', () => {
    render();

    const button = container.querySelector('[data-testid="carry-menu"]');
    expect(button?.getAttribute('aria-label')).toBe('Carry the shape over to other pages');
  });

  it('carries the shape to the following pages of the open page, by the step and no field', async () => {
    render();

    await choose('carry-following');

    expect(sdk.carry).toHaveBeenCalledTimes(1);
    expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
      path: {
        project_id: 'project',
        page_id: 'page',
        stage: 'geometry',
        step_id: 'step',
      },
      body: { scope: 'following', overwrite: false },
    });
    expect(sdk.carry.mock.calls[0]?.[0].path).not.toHaveProperty('name');
    expect(sdk.carry.mock.calls[0]?.[0].body).not.toHaveProperty('page_ids');
  });

  it('carries the shape to every page of the kind of the open page', async () => {
    render();

    await choose('carry-kind');

    expect(sdk.carry.mock.calls[0]?.[0].body).toEqual({ scope: 'kind', overwrite: false });
  });

  it('carries the shape to the selected pages without the open page, and counts them in the menu', async () => {
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

  it('writes over the pages that have a shape of their own when asked to', async () => {
    render(new Set(), true);

    await choose('carry-following');

    expect(sdk.carry.mock.calls[0]?.[0].body).toEqual({ scope: 'following', overwrite: true });
  });

  it('says how many pages took the shape and how many were skipped for a value of their own', async () => {
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

  it('shows the result and the undo the caller kept, as when the menu is drawn again for another page', () => {
    render(new Set(), false, CARRIED);

    expect(container.querySelector('[data-testid="carry-result"]')?.textContent).toContain(
      'Carried over to 2 pages',
    );
    expect(container.querySelector('[data-testid="carry-undo"]')).not.toBeNull();
    expect(sdk.carry).not.toHaveBeenCalled();
  });

  it('hands the result to the caller when the menu went away while the carry-over was on its way', async () => {
    let answer: (value: { data: CarryOverSchema }) => void = () => undefined;
    sdk.carry.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    render();
    await choose('carry-following');

    render(new Set(), false, null, false);
    await act(async () => answer({ data: CARRIED }));

    expect(container.querySelector('[data-testid="kept"]')?.textContent).toBe('batch');
  });

  it('offers no undo when no page took the shape', async () => {
    sdk.carry.mockResolvedValue({ data: { ...CARRIED, changes: [], skipped: ['own'] } });
    render();

    await choose('carry-following');

    expect(container.querySelector('[data-testid="carry-result"]')?.textContent).toContain(
      'Carried over to 0 pages',
    );
    expect(container.querySelector('[data-testid="carry-undo"]')).toBeNull();
  });
});
