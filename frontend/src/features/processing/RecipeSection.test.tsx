import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { row } from '@/features/workspace/fixtures';

/**
 * The content of the recipe slot of the panel on a stage that has no step bar: the steps as cards, with the plus button of
 * the bar to add one. No form of settings stands in a card, since the settings are in the frame of the panel, and the
 * profile, the choice of the recipe and the bar that saves it are not here, since `RecipeBar` draws them.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

const sdk = vi.hoisted(() => ({
  save: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPut: sdk.save,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** A stage without a step bar, which lists the steps of its recipe in the panel. */
const LISTED = 'page-split';

describe('RecipeSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(state: ReturnType<typeof processing>): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <RecipeSection processing={state} rows={[row('a', { recipe_id: 'r1' })]} />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    sdk.save.mockReset();
    sdk.save.mockResolvedValue({ data: recipe('made') });
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

  it('draws no profile, no choice of the recipe and no bar to save, which the recipe bar has', () => {
    render(processing({ stage: LISTED, dirty: true }));

    expect(byId('profile-button')).toBeNull();
    expect(byId('recipe-bar-select')).toBeNull();
    expect(byId('recipe-select')).toBeNull();
    expect(byId('recipe-save-bar')).toBeNull();
  });

  it('draws nothing while the stage has no recipe', () => {
    render(processing({ stage: LISTED, recipes: [], recipe: undefined }));

    expect(container.innerHTML).toBe('');
  });

  it('draws no form of settings in the open card, since the settings stand in the frame of the panel', () => {
    render(processing({ stage: LISTED, openId: 'step-0' }));

    expect(container.querySelector('form')).toBeNull();
    expect(container.textContent).not.toContain('Largest slant');
    expect(container.querySelector('h3')?.textContent).toBe('Steps');
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
});
