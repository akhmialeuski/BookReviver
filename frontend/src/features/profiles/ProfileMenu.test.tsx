import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { bodyOf, draftOf, toggleStep } from '@/features/processing/recipe';
import { profile, profilePage } from '@/features/profiles/fixtures';
import { ProfileMenu } from '@/features/profiles/ProfileMenu';

/**
 * The profile button of the recipe panel: the profile the recipe was made from, the mark that appears as soon as the steps
 * differ from it, and the menu that saves the steps to it, saves them as a new profile, or puts its steps back.
 */

const sdk = vi.hoisted(() => ({
  create: vi.fn(),
  list: vi.fn(),
  replace: vi.fn(),
  link: vi.fn(),
  recipes: vi.fn(),
  saveRecipe: vi.fn(),
  apply: vi.fn(),
  jobs: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  createProfileApiV1RecipeProfilesPost: sdk.create,
  listProfilesApiV1RecipeProfilesGet: sdk.list,
  putProfileApiV1RecipeProfilesProfileIdPut: sdk.replace,
  putRecipeProfileApiV1ProjectsProjectIdStagesStageRecipesRecipeIdProfilePut: sdk.link,
  listRecipesApiV1ProjectsProjectIdStagesStageRecipesGet: sdk.recipes,
  putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPut: sdk.saveRecipe,
  applyProfileApiV1ProjectsProjectIdRecipeProfilesProfileIdApplyPost: sdk.apply,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

const STEPS = [
  step('geometry.deskew', { params: { max_angle: 5, min_confidence: 0.3 } }),
  step('geometry.crop'),
];
const LINKED = recipe('r1', { profile_id: 'p1', steps: STEPS });
const PROFILE = profile('p1', { name: 'Photographed book', steps: STEPS, order: 'free' });

describe('ProfileMenu', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** The menu and the dialog are drawn into the body, outside the container the component is rendered into. */
  const byId = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  async function flush(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  async function render(
    state: ReturnType<typeof processing>,
    onManage?: () => void,
  ): Promise<void> {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ProfileMenu processing={state} onManage={onManage} />
        </QueryClientProvider>,
      ),
    );
    await flush();
  }

  async function click(id: string): Promise<void> {
    await act(async () => {
      byId(id)?.click();
    });
  }

  async function type(input: HTMLInputElement | null, value: string): Promise<void> {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(input, value);
      input?.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
    }
    sdk.list.mockResolvedValue(profilePage([PROFILE]));
    sdk.create.mockResolvedValue({ data: profile('made', { name: 'Clean flatbed scan' }) });
    sdk.replace.mockResolvedValue({ data: PROFILE });
    sdk.link.mockResolvedValue({ data: recipe('r1', { profile_id: 'made' }) });
    sdk.saveRecipe.mockResolvedValue({
      data: recipe('r1', {
        profile_id: 'p1',
        steps: [step('geometry.deskew', { step_id: 'saved-1' })],
      }),
    });
    sdk.recipes.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    sdk.apply.mockResolvedValue({
      data: {
        recipe: recipe('applied'),
        missing_processors: [],
        job: null,
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

  it('names the profile the recipe was made from, with no mark while the steps are the profile’s', async () => {
    await render(processing({ recipe: LINKED, steps: draftOf(LINKED) }));

    // The name comes with the list of profiles, which may answer after the first render
    await vi.waitFor(() => expect(byId('profile-name')?.textContent).toBe('Photographed book'));
    expect(byId('profile-changed')).toBeNull();
    expect(sdk.list).toHaveBeenCalledWith(
      expect.objectContaining({ query: { size: 100, stage: 'geometry' } }),
    );
  });

  it('says no profile for a recipe made from none, and offers only to save the steps as a new profile', async () => {
    await render(processing({ recipe: recipe('r1', { steps: STEPS }), steps: draftOf(LINKED) }));
    await click('profile-button');

    expect(byId('profile-name')?.textContent).toBe('No profile');
    expect(byId('profile-not-linked')).not.toBeNull();
    expect(byId('profile-save')).toHaveProperty('disabled', true);
    expect(byId('profile-revert')).toHaveProperty('disabled', true);
    expect(byId('profile-save-new')).toHaveProperty('disabled', false);
  });

  it('marks the book changed as soon as a step differs, and lists the differences in the menu', async () => {
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(processing({ recipe: LINKED, steps: off, dirty: true }));
    await click('profile-button');

    expect(byId('profile-changed')?.textContent).toBe('changed');
    expect(
      [...(byId('profile-changes')?.querySelectorAll('li') ?? [])].map((item) => item.textContent),
    ).toEqual(['geometry.crop switched off']);
  });

  it('offers the two ways of saving while the recipe has unsaved changes', async () => {
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(processing({ recipe: LINKED, steps: off, dirty: true }));
    await click('profile-button');

    expect(byId('profile-save')).toHaveProperty('disabled', false);
    expect(byId('profile-save-new')).toHaveProperty('disabled', false);
  });

  it('keeps the two ways of saving from steps that cannot be saved', async () => {
    await render(processing({ recipe: LINKED, steps: draftOf(LINKED), valid: false }));
    await click('profile-button');

    expect(byId('profile-save')).toHaveProperty('disabled', true);
    expect(byId('profile-save-new')).toHaveProperty('disabled', true);
  });

  it('saves the recipe first, with its order, and writes the profile from the saved steps', async () => {
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(processing({ recipe: LINKED, steps: off, dirty: true, orderMode: 'free' }));
    await click('profile-button');
    await click('profile-save');
    await flush();

    expect(sdk.saveRecipe).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { project_id: 'project', stage: 'geometry', recipe_id: 'r1' },
        body: { steps: bodyOf(off), order: 'free' },
      }),
    );
    expect(sdk.replace).toHaveBeenCalledWith(
      expect.objectContaining({
        body: {
          name: 'Photographed book',
          steps: [step('geometry.deskew', { step_id: 'saved-1' })],
          order: 'free',
        },
      }),
    );
    expect(sdk.saveRecipe.mock.invocationCallOrder[0]).toBeLessThan(
      sdk.replace.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it('writes no profile when the recipe could not be saved', async () => {
    sdk.saveRecipe.mockRejectedValue({ status: 422, detail: 'No' });
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(processing({ recipe: LINKED, steps: off, dirty: true }));
    await click('profile-button');
    await click('profile-save');
    await flush();

    expect(sdk.replace).not.toHaveBeenCalled();
    expect(byId('profile-saved')).toBeNull();
  });

  it('saves the recipe before it keeps the steps as a new profile', async () => {
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(
      processing({
        recipe: recipe('r1', { steps: STEPS }),
        steps: off,
        dirty: true,
      }),
    );
    await click('profile-button');
    await click('profile-save-new');
    await click('profile-save-submit');
    await flush();

    expect(sdk.saveRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ body: expect.objectContaining({ steps: bodyOf(off) }) }),
    );
    expect(sdk.create).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          steps: [step('geometry.deskew', { step_id: 'saved-1' })],
        }),
      }),
    );
    expect(sdk.link).toHaveBeenCalled();
  });

  it('saves the steps to the profile with the order the book is in', async () => {
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(processing({ recipe: LINKED, steps: off, orderMode: 'usual' }));
    await click('profile-button');
    await click('profile-save');

    expect(sdk.replace).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { profile_id: 'p1' },
        body: { name: 'Photographed book', steps: bodyOf(off), order: 'usual' },
      }),
    );
    await flush();
    expect(byId('profile-saved')?.textContent).toContain('Photographed book');
  });

  it('offers no saving to the profile when nothing differs', async () => {
    await render(processing({ recipe: LINKED, steps: draftOf(LINKED), orderMode: 'free' }));
    await click('profile-button');

    expect(byId('profile-save')).toHaveProperty('disabled', true);
    expect(byId('profile-revert')).toHaveProperty('disabled', true);
  });

  it('offers to save when only the order the book is in differs from the profile', async () => {
    await render(processing({ recipe: LINKED, steps: draftOf(LINKED), orderMode: 'usual' }));
    await click('profile-button');

    expect(byId('profile-save')).toHaveProperty('disabled', false);
  });

  it('puts the steps of the profile on the screen in the order the profile was saved in', async () => {
    const loadSteps = vi.fn();
    const off = toggleStep(draftOf(LINKED), 'step-1');
    await render(processing({ recipe: LINKED, steps: off, dirty: true, loadSteps }));
    await click('profile-button');
    await click('profile-revert');

    expect(loadSteps).toHaveBeenCalledWith(draftOf(PROFILE), 'free');
  });

  it('saves the steps as a new profile and links the recipe to it', async () => {
    await render(
      processing({
        recipe: recipe('r1', { steps: STEPS }),
        steps: draftOf(LINKED),
        orderMode: 'free',
      }),
    );
    await click('profile-button');
    await click('profile-save-new');
    const name = document.body.querySelector<HTMLInputElement>('input[name="profile-name"]');
    expect(name?.value).toBe('Text');
    await type(name, 'Clean flatbed scan');
    await click('profile-save-submit');
    await flush();

    expect(sdk.create).toHaveBeenCalledWith(
      expect.objectContaining({
        body: {
          stage: 'geometry',
          name: 'Clean flatbed scan',
          order: 'free',
          steps: bodyOf(draftOf(LINKED)),
        },
      }),
    );
    expect(sdk.link).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { project_id: 'project', stage: 'geometry', recipe_id: 'r1' },
        body: { profile_id: 'made' },
      }),
    );
    expect(byId('profile-saved')?.textContent).toContain('Clean flatbed scan');
  });

  it('does not link the recipe when the profile could not be saved', async () => {
    sdk.create.mockRejectedValue({ status: 422, detail: 'No' });
    await render(processing({ recipe: recipe('r1', { steps: STEPS }), steps: draftOf(LINKED) }));
    await click('profile-button');
    await click('profile-save-new');
    await click('profile-save-submit');
    await flush();

    expect(sdk.link).not.toHaveBeenCalled();
    expect(byId('profile-saved')).toBeNull();
  });

  it('offers no saving as a new profile while a value is outside its limits', async () => {
    await render(processing({ valid: false }));
    await click('profile-button');

    expect(byId('profile-save-new')).toHaveProperty('disabled', true);
  });
  describe('the other profiles of the stage and the library', () => {
    const OTHER = profile('p2', { name: 'Clean flatbed scan', is_default: true });

    it('lists the profiles of the stage other than the one of the recipe', async () => {
      sdk.list.mockResolvedValue(profilePage([PROFILE, OTHER]));

      await render(processing({ recipe: LINKED, steps: draftOf(LINKED) }));
      await click('profile-button');

      expect(
        [...document.body.querySelectorAll<HTMLElement>('[data-testid="profile-switch"]')].map(
          (item) => item.dataset.name,
        ),
      ).toEqual(['Clean flatbed scan']);
      expect(byId('profile-others-empty')).toBeNull();
    });

    it('says so when the stage has no other profile', async () => {
      await render(processing({ recipe: LINKED, steps: draftOf(LINKED) }));
      await click('profile-button');

      expect(byId('profile-others-empty')).not.toBeNull();
      expect(byId('profile-others')).toBeNull();
    });

    it('applies a listed profile to the recipe on screen, and opens it in the panel', async () => {
      sdk.list.mockResolvedValue(profilePage([PROFILE, OTHER]));
      const chooseRecipe = vi.fn();

      await render(processing({ recipe: LINKED, steps: draftOf(LINKED), chooseRecipe }));
      await click('profile-button');
      await click('profile-switch');
      await flush();

      expect(sdk.apply).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { project_id: 'project', profile_id: 'p2' },
          body: { kind: 'text' },
        }),
      );
      expect(chooseRecipe).toHaveBeenCalledWith('applied');
      expect(byId('profile-saved')?.textContent).toContain('Clean flatbed scan');
    });

    it('waits for the unsaved changes to be saved or reverted before it applies another profile', async () => {
      sdk.list.mockResolvedValue(profilePage([PROFILE, OTHER]));
      const off = toggleStep(draftOf(LINKED), 'step-1');

      await render(processing({ recipe: LINKED, steps: off, dirty: true }));
      await click('profile-button');

      expect(byId('profile-switch')).toHaveProperty('disabled', true);
    });

    it('opens the library from the entry of the menu', async () => {
      const onManage = vi.fn();

      await render(processing({ recipe: LINKED, steps: draftOf(LINKED) }), onManage);
      await click('profile-button');
      await click('profile-manage');

      expect(onManage).toHaveBeenCalledOnce();
    });

    it('has no entry for the library where it cannot be opened', async () => {
      await render(processing({ recipe: LINKED, steps: draftOf(LINKED) }));
      await click('profile-button');

      expect(byId('profile-manage')).toBeNull();
    });
  });
});
