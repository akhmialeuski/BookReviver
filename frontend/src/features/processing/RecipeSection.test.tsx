import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { setStepParams, toggleStep } from '@/features/processing/recipe';
import { profilePage } from '@/features/profiles/fixtures';
import { row } from '@/features/workspace/fixtures';

/**
 * The content of the recipe slot of the panel: the steps as cards on a stage that has no step bar, with the plus button of
 * the bar to add one, what a change of them costs, and the bar that saves it. A stage with a bar lists no steps here, and
 * no form of settings stands in a card on any stage, since the settings are in the frame of the panel.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

const sdk = vi.hoisted(() => ({
  save: vi.fn(),
  stages: vi.fn(),
  profiles: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPut: sdk.save,
  listStagesApiV1ProjectsProjectIdStagesGet: sdk.stages,
  listProfilesApiV1RecipeProfilesGet: sdk.profiles,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** A stage without a step bar, which lists the steps of its recipe in the panel. */
const LISTED = 'page-split';

const SAVED = recipe('r1', {
  steps: [step('geometry.deskew', { params: { max_angle: 5, min_confidence: 0.3 } })],
});

describe('RecipeSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(
    state: ReturnType<typeof processing>,
    rows = [row('a', { recipe_id: 'r1' })],
  ): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <RecipeSection processing={state} rows={rows} />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
      fake.mockResolvedValue({ data: recipe('made') });
    }
    sdk.profiles.mockResolvedValue(profilePage([]));
    sdk.stages.mockResolvedValue({
      data: {
        items: [
          {
            stage: 'geometry',
            available: true,
            manual: false,
            pages: 426,
            fresh: 426,
            stale: 0,
            failed: 0,
            not_run: 0,
            review: 0,
            check: 0,
            recipes: [{ kind: 'text', recipe_id: 'r1', pages: 426 }],
          },
        ],
        total: 1,
        page: 1,
        size: 50,
        pages: 1,
      },
    });
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

  it('lists the steps of the recipe', () => {
    render(processing({ stage: LISTED }));

    expect(container.querySelectorAll('[data-testid="recipe-step"]')).toHaveLength(1);
    const card = container.querySelector('[data-testid="recipe-step"]')?.textContent;
    expect(card).toContain('Deskew');
    expect(card).not.toMatch(/\d ·/);
  });

  it('puts the button of the profile above the recipes, naming none for a recipe made from no profile', () => {
    render(processing());

    expect(byId('profile-button')?.textContent).toContain('No profile');
    expect(byId('profile-changed')).toBeNull();
  });

  it.each(['geometry', 'cleanup'] as const)(
    'lists no steps on %s, whose steps are in the step bar, and keeps the rest of the recipe',
    (stage) => {
      render(processing({ stage, orderMode: 'free' }));

      expect(byId('recipe-select')).toBeNull();
      expect(byId('recipe-steps')).toBeNull();
      expect(byId('recipe-step')).toBeNull();
      expect(byId('step-catalogue')).toBeNull();
      expect(byId('order-free')).toBeNull();
    },
  );

  it('keeps the save bar of a changed recipe on a stage whose steps are in the step bar', () => {
    render(processing({ dirty: true }));

    expect(byId('recipe-save')?.disabled).toBe(false);
  });

  it('draws no form of settings in the open card, since the settings stand in the frame of the panel', () => {
    render(processing({ stage: LISTED, openId: 'step-0' }));

    expect(container.querySelector('form')).toBeNull();
    expect(container.textContent).not.toContain('Largest slant');
    expect(container.querySelector('h3')?.textContent).toBe('Steps');
  });

  it('draws the choice of the recipe in the panel only on a stage without a step bar, since the bar has it otherwise', () => {
    const picture = recipe('r2', { kind: 'color-picture' });
    const kinds = { recipes: [SAVED, picture] };

    render(processing({ stage: LISTED, ...kinds }));
    expect(container.querySelectorAll('[data-testid="recipe-select"] option')).toHaveLength(2);

    render(processing({ stage: 'geometry', ...kinds }));
    expect(byId('recipe-select')).toBeNull();
  });

  it('has no bar to save while the draft is the saved recipe', () => {
    render(processing());

    expect(byId('recipe-save-bar')).toBeNull();
  });

  it('says how many pages a save makes out of date', () => {
    const steps = setStepParams(processing().steps, 'step-0', {
      max_angle: 9,
      min_confidence: 0.3,
    });
    render(processing({ dirty: true, steps }), [
      row('a', { recipe_id: 'r1', status: 'fresh' }),
      row('b', { recipe_id: 'r1', status: 'fresh' }),
      row('c', { recipe_id: 'r1', status: 'stale' }),
    ]);

    expect(byId('recipe-stale-warning')?.textContent).toBe('Saving makes 2 pages out of date.');
  });

  it('saves the recipe with the steps as the draft has them', async () => {
    const steps = toggleStep(
      setStepParams(processing().steps, 'step-0', { max_angle: 9, min_confidence: 0.3 }),
      'step-0',
    );
    render(processing({ dirty: true, steps }));

    await act(async () => {
      byId('recipe-save')?.click();
    });

    expect(sdk.save).toHaveBeenCalledTimes(1);
    expect(sdk.save.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', recipe_id: 'r1' },
      body: {
        steps: [
          {
            processor_key: 'geometry.deskew',
            params: { max_angle: 9, min_confidence: 0.3 },
            enabled: false,
          },
        ],
      },
    });
  });

  it('sends the order the recipe is kept in with its steps', async () => {
    render(processing({ dirty: true, orderMode: 'free' }));

    await act(async () => {
      byId('recipe-save')?.click();
    });

    expect(sdk.save.mock.calls[0]?.[0]).toMatchObject({ body: { order: 'free' } });
  });

  it('does not let a step that stands where it cannot work be saved in the usual order', () => {
    render(
      processing({
        dirty: true,
        refused: [
          {
            stepId: 'step-0',
            otherId: 'step-1',
            kind: 'required',
            reason: 'Margins cannot come before Select content.',
          },
        ],
      }),
    );

    expect(byId('recipe-save')?.disabled).toBe(true);
    expect(byId('recipe-order-blocked')?.textContent).toContain('cannot be saved');
  });

  it('turns the free order on and off with its switch', () => {
    const setOrderMode = vi.fn();
    render(processing({ stage: LISTED, setOrderMode }));
    act(() => byId('order-free')?.click());
    expect(setOrderMode).toHaveBeenLastCalledWith('free');

    render(processing({ stage: LISTED, orderMode: 'free', setOrderMode }));
    act(() => byId('order-free')?.click());
    expect(setOrderMode).toHaveBeenLastCalledWith('usual');
  });

  it('does not let a value outside its limits be saved', () => {
    render(processing({ dirty: true, valid: false }));

    expect(byId('recipe-save')?.disabled).toBe(true);
    expect(container.textContent).toContain('A value is outside its limits');
  });

  it('adds a step with the plus button of the step bar, which has no word, and opens the new card', async () => {
    sdk.save.mockResolvedValue({
      data: recipe('r1', {
        steps: [
          step('geometry.deskew', { step_id: 'a' }),
          step('geometry.deskew', { step_id: 'b' }),
        ],
      }),
    });
    const open = vi.fn();
    render(processing({ stage: LISTED, open }));

    const plus = byId('step-catalogue');
    expect(plus?.textContent).toBe('');
    expect(plus?.getAttribute('aria-label')).toBe('Add a step');
    await act(async () => {
      plus?.click();
    });
    const entries = [
      ...document.body.querySelectorAll<HTMLElement>('[data-testid="catalogue-add"]'),
    ];
    expect(entries.map((entry) => entry.getAttribute('data-processor'))).toEqual([
      'geometry.deskew',
    ]);

    await act(async () => {
      entries[0]?.click();
    });
    expect(sdk.save).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('step-1');
  });

  it('shows the answer of the server when a save is refused', async () => {
    sdk.save.mockRejectedValue(new Error('refused'));
    render(processing({ dirty: true }));

    await act(async () => {
      byId('recipe-save')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
});
