import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RecipeKind, StageSummarySchema } from '@/api';
import { listStagesApiV1ProjectsProjectIdStagesGetOptions } from '@/api/@tanstack/react-query.gen';
import { deskew, processing, processor, recipe, step } from '@/features/processing/fixtures';
import { RecipePicker } from '@/features/processing/RecipePicker';
import { StepBar } from '@/features/workspace/StepBar';
import { barStepsOf } from '@/features/workspace/steps';

/**
 * The choice of the recipe of a stage: each kind of page with the number of the pages of the book that are of it, and
 * what a choice does, which is to show the steps of that recipe and nothing else.
 */

const sdk = vi.hoisted(() => ({ run: vi.fn(), save: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPut: sdk.save,
}));

const KINDS: readonly RecipeKind[] = ['text', 'color-picture', 'bw-picture', 'blank'];
const RECIPES = KINDS.map((kind, index) => recipe(`r${index}`, { kind }));
const PAGES = [12, 3, 0, 1];
const SUMMARY: StageSummarySchema = {
  stage: 'geometry',
  available: true,
  manual: false,
  pages: 16,
  fresh: 0,
  stale: 0,
  failed: 0,
  not_run: 16,
  review: 0,
  check: 0,
  partial: 0,
  recipes: KINDS.map((kind, index) => ({
    kind,
    recipe_id: `r${index}`,
    pages: PAGES[index] ?? 0,
  })),
  stopped: [],
};

describe('RecipePicker', () => {
  let container: HTMLDivElement;
  let root: Root;
  const chooseRecipe = vi.fn();

  /** Draw an element with the summary of the stage already read, so the picker draws from it without a server. */
  function mount(element: React.JSX.Element): void {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    client.setQueryData(
      listStagesApiV1ProjectsProjectIdStagesGetOptions({ path: { project_id: 'project' } })
        .queryKey,
      { items: [SUMMARY], total: 1, page: 1, size: 100, pages: 1 },
    );
    act(() => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>));
  }

  function render(recipes = RECIPES): void {
    mount(<RecipePicker processing={processing({ recipe: RECIPES[0], recipes, chooseRecipe })} />);
  }

  function choose(id: string): void {
    act(() => {
      const element = select();
      if (element !== null) {
        element.value = id;
        element.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  }

  const select = (): HTMLSelectElement | null =>
    container.querySelector<HTMLSelectElement>('[data-testid="recipe-select"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    chooseRecipe.mockReset();
    sdk.run.mockReset();
    sdk.save.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('lists the four kinds of page, each with the pages of the book that are of it', () => {
    render();

    expect([...(select()?.options ?? [])].map((option) => option.textContent)).toEqual([
      'Text · 12 pages',
      'Colour picture · 3 pages',
      'Black-and-white picture · 0 pages',
      'Blank page · 1 page',
    ]);
    expect(select()?.value).toBe('r0');
  });

  it('draws nothing for a stage with one recipe, which has nothing to choose', () => {
    render([RECIPES[0] ?? recipe('r0')]);

    expect(select()).toBeNull();
  });

  it('reports the recipe chosen, and starts no run and saves nothing', () => {
    render();

    choose('r3');

    expect(chooseRecipe).toHaveBeenCalledWith('r3');
    expect(sdk.run).not.toHaveBeenCalled();
    expect(sdk.save).not.toHaveBeenCalled();
  });

  it('draws only the choice, with no list of the rules that send pages to a recipe and no menu that applies it', () => {
    render();

    expect(container.textContent).not.toContain('Used for');
    expect(container.textContent).not.toMatch(/Apply .* to/);
    expect(container.querySelectorAll('select, button')).toHaveLength(1);
  });

  it('shows the steps of the recipe that is chosen in the bar of the steps', () => {
    const catalogue = [processor('geometry.perspective', { title: 'Perspective' }), deskew()];
    const bySteps = [
      recipe('r0', { kind: 'text', steps: [step('geometry.deskew', { step_id: 'text-deskew' })] }),
      recipe('r1', {
        kind: 'color-picture',
        steps: [
          step('geometry.perspective', { step_id: 'colour-perspective' }),
          step('geometry.deskew', { step_id: 'colour-deskew' }),
        ],
      }),
    ];

    function Shown(): React.JSX.Element {
      const [id, setId] = useState('r0');
      const shown = bySteps.find((entry) => entry.id === id);
      return (
        <>
          <RecipePicker
            processing={processing({ recipe: shown, recipes: bySteps, chooseRecipe: setId })}
          />
          <StepBar
            steps={barStepsOf(shown, catalogue)}
            openId={undefined}
            states={new Map()}
            onOpen={vi.fn()}
          />
        </>
      );
    }
    const stepIds = (): (string | null)[] =>
      [...container.querySelectorAll('[data-testid="bar-step"]')].map((button) =>
        button.getAttribute('data-step-id'),
      );

    mount(<Shown />);
    expect(stepIds()).toEqual(['text-deskew']);

    choose('r1');

    expect(stepIds()).toEqual(['colour-perspective', 'colour-deskew']);
  });
});
