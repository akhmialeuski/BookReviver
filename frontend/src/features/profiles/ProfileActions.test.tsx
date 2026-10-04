import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe } from '@/features/processing/fixtures';
import { profile, profilePage } from '@/features/profiles/fixtures';
import { ProfileActions } from '@/features/profiles/ProfileActions';

/**
 * The button that applies a profile of the account to the open book, as a variant or as the active recipe, and what the
 * panel says about the steps the server had to leave out. Saving the steps as a profile is the profile menu.
 */

const sdk = vi.hoisted(() => ({
  list: vi.fn(),
  apply: vi.fn(),
  recipes: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listProfilesApiV1RecipeProfilesGet: sdk.list,
  applyProfileApiV1ProjectsProjectIdRecipeProfilesProfileIdApplyPost: sdk.apply,
  listVariantsApiV1ProjectsProjectIdStagesStageVariantsGet: sdk.recipes,
}));

describe('ProfileActions', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** The dialogs are drawn into the body, outside the container the component is rendered into. */
  const byId = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  function render(state: ReturnType<typeof processing>): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ProfileActions processing={state} />
        </QueryClientProvider>,
      ),
    );
  }

  async function click(id: string): Promise<void> {
    await act(async () => {
      byId(id)?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
    }
    sdk.list.mockResolvedValue(
      profilePage([profile('p1', { name: 'Photographed book', is_default: true })]),
    );
    sdk.apply.mockResolvedValue({
      data: { recipe: recipe('applied', { active: false }), missing_processors: [] },
    });
    sdk.recipes.mockResolvedValue({
      data: { items: [], total: 0, page: 1, size: 100, pages: 1 },
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

  it('lists the profiles of the stage and applies the chosen one as a variant', async () => {
    const choose = vi.fn();
    render(processing({ chooseRecipe: choose }));

    await click('profile-apply-open');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const select = byId('profile-apply-select') as HTMLSelectElement | null;
    expect(select?.textContent).toBe('Photographed book · default · 2 steps');
    await click('profile-apply-submit');

    expect(sdk.list).toHaveBeenCalledWith(
      expect.objectContaining({ query: { size: 100, stage: 'geometry' } }),
    );
    expect(sdk.apply).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { project_id: 'project', profile_id: 'p1' },
        body: { activate: false },
      }),
    );
    expect(choose).toHaveBeenCalledWith('applied');
    expect(byId('profile-applied')?.textContent).toContain('as a variant');
  });

  it('asks for the variant to be the active recipe when the box is ticked', async () => {
    render(processing());

    await click('profile-apply-open');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await click('profile-apply-activate');
    await click('profile-apply-submit');

    expect(sdk.apply).toHaveBeenCalledWith(expect.objectContaining({ body: { activate: true } }));
  });

  it('names the processors whose steps the server left out', async () => {
    sdk.apply.mockResolvedValue({
      data: { recipe: recipe('applied', { active: false }), missing_processors: ['geometry.gone'] },
    });
    render(processing());

    await click('profile-apply-open');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await click('profile-apply-submit');

    expect(byId('profile-left-out')?.textContent).toContain('geometry.gone');
  });

  it('says there is nothing to apply when the account has no profile for the stage', async () => {
    sdk.list.mockResolvedValue(profilePage([]));
    render(processing());

    await click('profile-apply-open');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(byId('profile-apply-empty')).not.toBeNull();
    expect(byId('profile-apply-submit')).toHaveProperty('disabled', true);
  });
});
