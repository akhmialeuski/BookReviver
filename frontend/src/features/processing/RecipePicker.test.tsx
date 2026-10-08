import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RecipeKind, StageSummarySchema } from '@/api';
import { listStagesApiV1ProjectsProjectIdStagesGetOptions } from '@/api/@tanstack/react-query.gen';
import { processing, recipe } from '@/features/processing/fixtures';
import { RecipePicker } from '@/features/processing/RecipePicker';

/** The choice of the recipe of a stage: each kind of page with the number of the pages of the book that are of it. */

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

  function render(recipes = RECIPES): void {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
    client.setQueryData(
      listStagesApiV1ProjectsProjectIdStagesGetOptions({ path: { project_id: 'project' } })
        .queryKey,
      { items: [SUMMARY], total: 1, page: 1, size: 100, pages: 1 },
    );
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <RecipePicker processing={processing({ recipe: RECIPES[0], recipes, chooseRecipe })} />
        </QueryClientProvider>,
      ),
    );
  }

  const select = (): HTMLSelectElement | null =>
    container.querySelector<HTMLSelectElement>('[data-testid="recipe-select"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    chooseRecipe.mockReset();
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

  it('reports the recipe chosen', () => {
    render();

    act(() => {
      const element = select();
      if (element !== null) {
        element.value = 'r3';
        element.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });

    expect(chooseRecipe).toHaveBeenCalledWith('r3');
  });
});
