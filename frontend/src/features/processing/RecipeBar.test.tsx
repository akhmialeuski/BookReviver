import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { RecipeBar } from '@/features/processing/RecipeBar';
import { setStepParams, toggleStep } from '@/features/processing/recipe';
import { profilePage } from '@/features/profiles/fixtures';
import { row } from '@/features/workspace/fixtures';

/**
 * The bar of the profile and the recipe, which the top of the panel and the window of the gear both draw: one row with the
 * profile and the choice of the recipe, the amber row that saves the recipe under it while it has unsaved changes, and the
 * way from the profile button to the library of profiles.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in, and the side
 * panel of the library is replaced by one that only says it is open, so nothing reaches the server.
 */

const sdk = vi.hoisted(() => ({
  save: vi.fn(),
  stages: vi.fn(),
  profiles: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putRecipeApiV1BooksBookIdStagesStageRecipesRecipeIdPut: sdk.save,
  listStagesApiV1BooksBookIdStagesGet: sdk.stages,
  listProfilesApiV1BooksBookIdRecipeProfilesGet: sdk.profiles,
}));
vi.mock('@/features/profiles/ProfileLibraryPanel', () => ({
  ProfileLibraryPanel: ({ open }: { open: boolean }) =>
    open ? <div data-testid="profile-library-panel" /> : null,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const SAVED = recipe('r1', {
  steps: [step('geometry.deskew', { params: { max_angle: 5, min_confidence: 0.3 } })],
});
const PICTURES = recipe('r2', { kind: 'color-picture' });

describe('RecipeBar', () => {
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
          <RecipeBar processing={state} rows={rows} />
        </QueryClientProvider>,
      ),
    );
  }

  /** The menu of the profile is drawn into the body, outside the container the bar is rendered into. */
  const byId = (id: string): HTMLButtonElement | null =>
    document.body.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`);

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
            recipes: [
              { kind: 'text', recipe_id: 'r1', pages: 420 },
              { kind: 'color-picture', recipe_id: 'r2', pages: 6 },
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

  describe('with no changes', () => {
    it('is one row with the button of the profile, naming none for a recipe made from no profile', () => {
      render(processing());

      expect(byId('recipe-bar')).not.toBeNull();
      expect(byId('profile-button')?.textContent).toContain('No profile');
      expect(byId('profile-changed')).toBeNull();
      expect(byId('recipe-save-bar')).toBeNull();
    });

    it('draws no choice of the recipe while the stage has one recipe', () => {
      render(processing());

      expect(byId('recipe-bar-select')).toBeNull();
    });

    it('puts the choice of the recipe beside the profile, each option with the number of its pages', async () => {
      render(processing({ recipes: [SAVED, PICTURES], recipe: SAVED }));
      await vi.waitFor(() =>
        expect(
          [...document.querySelectorAll('[data-testid="recipe-bar-select"] option')].map(
            (option) => option.textContent,
          ),
        ).toEqual(['Text · 420 pages', 'Colour picture · 6 pages']),
      );

      const firstRow = byId('recipe-bar')?.firstElementChild;
      expect(firstRow?.contains(byId('profile-button'))).toBe(true);
      expect(firstRow?.contains(byId('recipe-bar-select'))).toBe(true);
    });

    it('chooses another recipe from the select', async () => {
      const chooseRecipe = vi.fn();
      render(processing({ recipes: [SAVED, PICTURES], recipe: SAVED, chooseRecipe }));

      const select = document.body.querySelector<HTMLSelectElement>(
        '[data-testid="recipe-bar-select"]',
      );
      await act(async () => {
        if (select !== null) {
          select.value = 'r2';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });

      expect(chooseRecipe).toHaveBeenCalledWith('r2');
    });

    it('draws nothing while the stage has no recipe', () => {
      render(processing({ recipes: [], recipe: undefined }));

      expect(byId('recipe-bar')).toBeNull();
    });
  });

  describe('with unsaved changes', () => {
    it('draws the amber row with Save and Discard right under the first row', () => {
      render(processing({ dirty: true }));

      const bar = byId('recipe-bar');
      expect(bar?.children).toHaveLength(2);
      expect(bar?.lastElementChild).toBe(byId('recipe-save-bar'));
      expect(byId('recipe-save')?.textContent).toBe('Save');
      expect(byId('recipe-save')?.disabled).toBe(false);
      expect(byId('recipe-discard')?.textContent).toBe('Discard');
    });

    it('goes back to the saved recipe with Discard', () => {
      const discard = vi.fn();
      render(processing({ dirty: true, discard }));

      act(() => byId('recipe-discard')?.click());

      expect(discard).toHaveBeenCalledTimes(1);
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
        path: { book_id: 'book', stage: 'geometry', recipe_id: 'r1' },
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

    it('does not let a value outside its limits be saved', () => {
      render(processing({ dirty: true, valid: false }));

      expect(byId('recipe-save')?.disabled).toBe(true);
      expect(container.textContent).toContain('A value is outside its limits');
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

  describe('the menu of the profile', () => {
    it('opens the library of profiles from the entry that manages them', async () => {
      render(processing());
      expect(byId('profile-library-panel')).toBeNull();

      await act(async () => {
        byId('profile-button')?.click();
      });
      expect(byId('profile-menu')).not.toBeNull();

      await act(async () => {
        byId('profile-manage')?.click();
      });

      expect(byId('profile-library-panel')).not.toBeNull();
    });
  });
});
