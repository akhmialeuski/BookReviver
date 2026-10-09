import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, processor, recipe, step } from '@/features/processing/fixtures';
import { draftOf } from '@/features/processing/recipe';
import { row } from '@/features/workspace/fixtures';
import { StepCatalogue } from '@/features/workspace/StepCatalogue';
import { MESSAGES } from '@/shared/messages';

/**
 * The catalogue the plus button of the bar opens: the processors of the stage with what each does, and the choice that adds a
 * step to the saved recipe where its processor usually stands, which is refused in the usual order when no place suits.
 *
 * The processors here declare the places the catalogue of the server does: B usually follows A, and D and E must each
 * follow the other, so a step of one of them has no place beside a step of the other.
 */

const sdk = vi.hoisted(() => ({ save: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPut: sdk.save,
}));

const A_REASON = 'B reads what A leaves, so it usually comes after A.';
const D_REASON = 'D works on what E leaves, so it cannot come before E.';
const E_REASON = 'E works on what D leaves, so it cannot come before D.';

const CATALOGUE = [
  processor('geometry.a', { title: 'A', summary: 'Does what A does' }),
  processor('geometry.b', {
    title: 'B',
    after: [{ processor_key: 'geometry.a', reason: A_REASON }],
  }),
  processor('geometry.d', {
    title: 'D',
    requires_after: [{ processor_key: 'geometry.e', reason: D_REASON }],
  }),
  processor('geometry.e', {
    title: 'E',
    requires_after: [{ processor_key: 'geometry.d', reason: E_REASON }],
  }),
];

const SAVED = recipe('r1', {
  steps: [step('geometry.a', { step_id: 'sa' }), step('geometry.b', { step_id: 'sb' })],
});

describe('StepCatalogue', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const onAdded = vi.fn();

  function render(overrides: Parameters<typeof processing>[0] = {}, saved = SAVED): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <StepCatalogue
            processing={processing({
              catalogue: CATALOGUE,
              recipe: saved,
              recipes: [saved],
              steps: draftOf(saved),
              ...overrides,
            })}
            rows={[row('a', { recipe_id: saved.id, status: 'fresh' })]}
            onAdded={onAdded}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  async function open(): Promise<void> {
    await act(async () => {
      byId('step-catalogue')?.click();
    });
  }

  async function choose(key: string): Promise<void> {
    await act(async () => {
      document.body.querySelector<HTMLElement>(`[data-processor="${key}"]`)?.click();
    });
    // The answer of the save reaches the mutation a few ticks after the click
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.save.mockReset();
    onAdded.mockReset();
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

  it('is a plus with no word beside it, named for readers of the screen', () => {
    render();

    const button = byId('step-catalogue');
    expect(button?.textContent).toBe('');
    expect(button?.getAttribute('aria-label')).toBe(MESSAGES.workspace.steps.catalogue.open);
    expect(button?.getAttribute('title')).toBe(MESSAGES.workspace.steps.catalogue.openHint);
  });

  it('lists every processor of the stage with its line, and names the stage in its title', async () => {
    render();
    await open();

    const entries = [...document.body.querySelectorAll('[data-testid="catalogue-add"]')];
    expect(entries.map((entry) => entry.getAttribute('data-processor'))).toEqual([
      'geometry.a',
      'geometry.b',
      'geometry.d',
      'geometry.e',
    ]);
    expect(entries[0]?.textContent).toContain('Does what A does');
    expect(byId('step-catalogue-list')?.textContent).toContain('Add a geometry step');
    expect(byId('step-catalogue-list')?.textContent).toContain('more than once');
  });

  it('says how many pages the new step makes out of date', async () => {
    render();
    await open();

    expect(byId('catalogue-stale')?.textContent).toBe('Adding a step makes 1 page out of date.');
  });

  it('adds a step of a processor the recipe has already, where the processor usually stands', async () => {
    sdk.save.mockResolvedValue({
      data: recipe('r1', {
        steps: [
          step('geometry.a', { step_id: 'sa' }),
          step('geometry.a', { step_id: 'new' }),
          step('geometry.b', { step_id: 'sb' }),
        ],
      }),
    });
    render();
    await open();
    await choose('geometry.a');

    const body = sdk.save.mock.calls[0]?.[0]?.body;
    expect(body.steps.map((entry: { processor_key: string }) => entry.processor_key)).toEqual([
      'geometry.a',
      'geometry.a',
      'geometry.b',
    ]);
    expect(body.steps.map((entry: { step_id: string | null }) => entry.step_id)).toEqual([
      'sa',
      null,
      'sb',
    ]);
    expect(body).toMatchObject({ order: 'usual' });
    expect(onAdded).toHaveBeenCalledWith('new', 1);
    expect(byId('step-catalogue-list')).toBeNull();
  });

  it('puts a step at the end when nothing asks for another place', async () => {
    sdk.save.mockResolvedValue({ data: SAVED });
    render();
    await open();
    await choose('geometry.b');

    const keys = sdk.save.mock.calls[0]?.[0]?.body.steps.map(
      (entry: { processor_key: string }) => entry.processor_key,
    );
    expect(keys).toEqual(['geometry.a', 'geometry.b', 'geometry.b']);
  });

  it('sends nothing for a place the usual order refuses, and says why', async () => {
    const saved = recipe('r1', { steps: [step('geometry.e', { step_id: 'se' })] });
    render({}, saved);
    await open();
    await choose('geometry.d');

    expect(sdk.save).not.toHaveBeenCalled();
    expect(byId('catalogue-refused')?.textContent).toContain('cannot come before');
    expect(onAdded).not.toHaveBeenCalled();
  });

  it('sends the step in the free order, which only warns', async () => {
    const saved = recipe('r1', { steps: [step('geometry.e', { step_id: 'se' })] });
    sdk.save.mockResolvedValue({ data: saved });
    render({ orderMode: 'free' }, saved);
    await open();
    await choose('geometry.d');

    expect(sdk.save).toHaveBeenCalledTimes(1);
    expect(sdk.save.mock.calls[0]?.[0]).toMatchObject({ body: { order: 'free' } });
  });

  it('waits while the draft of the panel has changes, so they are not saved with the step', async () => {
    render({ dirty: true });
    await open();
    await choose('geometry.a');

    expect(sdk.save).not.toHaveBeenCalled();
    expect(byId('catalogue-dirty')?.textContent).toContain('not saved');
    expect(
      document.body.querySelector<HTMLButtonElement>('[data-testid="catalogue-add"]')?.disabled,
    ).toBe(true);
  });

  it('lists a planned step with the word Soon until its processor is installed', async () => {
    render({ catalogue: [processor('geometry.deskew', { title: 'Deskew' })] });
    await open();

    expect(byId('catalogue-soon')?.textContent).toContain('Soon');
  });
});
