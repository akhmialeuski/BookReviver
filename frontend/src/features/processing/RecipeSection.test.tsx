import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processing, recipe, step } from '@/features/processing/fixtures';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { setStepParams, toggleStep } from '@/features/processing/recipe';
import { row } from '@/features/workspace/fixtures';

/**
 * The recipe in the panel: its steps with their settings, what a change of them costs, and the buttons that save it, copy
 * it and make it the one the stage runs by.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

const sdk = vi.hoisted(() => ({
  save: vi.fn(),
  create: vi.fn(),
  activate: vi.fn(),
  rules: vi.fn(),
  stages: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putVariantApiV1ProjectsProjectIdStagesStageVariantsRecipeIdPut: sdk.save,
  createVariantApiV1ProjectsProjectIdStagesStageVariantsPost: sdk.create,
  activateVariantApiV1ProjectsProjectIdStagesStageVariantsRecipeIdActivatePost: sdk.activate,
  listRulesApiV1ProjectsProjectIdStagesStageRulesGet: sdk.rules,
  listStagesApiV1ProjectsProjectIdStagesGet: sdk.stages,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const SAVED = recipe('r1', {
  name: 'Deskew',
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
    sdk.rules.mockResolvedValue({
      data: {
        items: [
          {
            id: 'rule',
            project_id: 'project',
            stage: 'geometry',
            condition: 'plates',
            group_label: '',
            recipe_id: 'r2',
            order: 0,
          },
        ],
        total: 1,
        page: 1,
        size: 100,
        pages: 1,
      },
    });
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
            active_recipe_id: 'r1',
            variants: [
              { recipe_id: 'r1', pages: 412 },
              { recipe_id: 'r2', pages: 14 },
            ],
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

  it('shows the recipe, whether it is active, and how many pages it has made', () => {
    render(processing());

    expect(byId('recipe-select')?.textContent).toBe('Deskew · active · 1 page');
    expect(byId('recipe-active')?.textContent).toBe('Active · 1 page');
    expect(container.querySelectorAll('[data-testid="recipe-step"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="recipe-step"]')?.textContent).toContain(
      '1 · Deskew',
    );
  });

  it('lists the steps that are coming under the steps that exist, and drops one the catalogue has', () => {
    render(processing());
    expect(container.querySelector('[data-testid="coming-steps"]')?.textContent).toContain(
      'Dewarp by mesh',
    );

    render(
      processing({
        catalogue: [deskew(), { ...deskew(), key: 'geometry.dewarp', title: 'Dewarp' }],
      }),
    );
    expect(container.querySelector('[data-testid="coming-steps"]')?.textContent).not.toContain(
      'Dewarp by mesh',
    );
  });

  it('draws the settings of the open step from the schema, with the titles of its fields', () => {
    const open = processing({ openId: 'step-0' });
    render(open);

    expect(container.querySelector('form')?.textContent).toContain('Largest slant');
    expect(container.querySelector('form')?.textContent).toContain('Least confidence');
  });

  it('counts the pages of each variant in a line of the names, and shows what the variant is used for', async () => {
    const plates = recipe('r2', { name: 'Plates', active: false });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <RecipeSection
            processing={processing({ recipe: plates, recipes: [SAVED, plates] })}
            rows={[row('a', { recipe_id: 'r1' }), row('b', { recipe_id: 'r2' })]}
          />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(byId('variant-counts')?.textContent).toBe('Deskew 412 · Plates 14');
    expect(byId('used-for')?.textContent).toContain('Plates and frontispieces');
    expect(byId('used-for-pages')?.textContent).toBe('Made 1 page');
  });

  it('draws no line of counts for a stage with a single variant', () => {
    render(processing());

    expect(byId('variant-counts')).toBeNull();
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

  it('saves the recipe under its own name with the steps as the draft has them', async () => {
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
        name: 'Deskew',
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
    render(processing({ setOrderMode }));
    act(() => byId('order-free')?.click());
    expect(setOrderMode).toHaveBeenLastCalledWith('free');

    render(processing({ orderMode: 'free', setOrderMode }));
    act(() => byId('order-free')?.click());
    expect(setOrderMode).toHaveBeenLastCalledWith('usual');
  });

  it('does not let a value outside its limits be saved', () => {
    render(processing({ dirty: true, valid: false }));

    expect(byId('recipe-save')?.disabled).toBe(true);
    expect(container.textContent).toContain('A value is outside its limits');
  });

  it('offers the processors of the stage in the menu of the steps to add, and adds the one chosen', async () => {
    const add = vi.fn();
    render(processing({ add }));

    await act(async () => {
      const trigger = byId('step-add');
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    const items = [...document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')];
    expect(items.map((item) => item.textContent)).toEqual(['Deskew']);

    await act(async () => {
      items[0]?.click();
    });
    expect(add).toHaveBeenCalledWith(expect.objectContaining({ key: 'geometry.deskew' }));
  });

  it('copies the recipe as shown, draft included, under a name of its own, and shows the copy', async () => {
    const chooseRecipe = vi.fn();
    const steps = setStepParams(processing().steps, 'step-0', {
      max_angle: 9,
      min_confidence: 0.3,
    });
    render(processing({ dirty: true, steps, chooseRecipe }));

    await act(async () => {
      byId('recipe-new')?.click();
    });

    expect(sdk.create.mock.calls[0]?.[0].body).toMatchObject({
      name: 'Deskew (copy)',
      steps: [{ processor_key: 'geometry.deskew', params: { max_angle: 9 } }],
    });
    expect(chooseRecipe).toHaveBeenCalledWith('made');
  });

  it('makes a variant the recipe the stage runs by', async () => {
    const variant = recipe('r2', { name: 'Gentle', active: false });
    render(processing({ recipe: variant, recipes: [SAVED, variant], steps: processing().steps }));
    expect(byId('recipe-active')).toBeNull();

    await act(async () => {
      byId('recipe-use')?.click();
    });

    expect(sdk.activate.mock.calls[0]?.[0].path).toEqual({
      project_id: 'project',
      stage: 'geometry',
      recipe_id: 'r2',
    });
  });

  it('offers no way to activate the recipe that is active', () => {
    render(processing());

    expect(byId('recipe-use')).toBeNull();
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
