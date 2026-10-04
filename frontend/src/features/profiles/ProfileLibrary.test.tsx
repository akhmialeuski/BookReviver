import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processing, processor, recipe, step } from '@/features/processing/fixtures';
import { profile, profilePage } from '@/features/profiles/fixtures';
import { ProfileLibrary } from '@/features/profiles/ProfileLibrary';
import { ProfileLibraryPanel } from '@/features/profiles/ProfileLibraryPanel';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';
import { parseProblem } from '@/shared/http/problem';

/**
 * The library of profiles: a tab for each stage with profiles, a card for each profile with its books and its steps, the
 * application of a profile to the book and to the selected pages, the copy, the file that is saved and the file that is
 * read, and the same list without a book.
 *
 * The generated client is replaced by functions the test reads, so each request is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({
  list: vi.fn(),
  processors: vi.fn(),
  apply: vi.fn(),
  duplicate: vi.fn(),
  exporter: vi.fn(),
  importer: vi.fn(),
  jobs: vi.fn(),
  recipes: vi.fn(),
}));

const files = vi.hoisted(() => ({ save: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listProfilesApiV1RecipeProfilesGet: sdk.list,
  listProcessorsApiV1ProcessorsGet: sdk.processors,
  applyProfileApiV1ProjectsProjectIdRecipeProfilesProfileIdApplyPost: sdk.apply,
  duplicateProfileApiV1RecipeProfilesProfileIdDuplicatePost: sdk.duplicate,
  exportProfileApiV1RecipeProfilesProfileIdExportGet: sdk.exporter,
  importProfileApiV1RecipeProfilesImportPost: sdk.importer,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listVariantsApiV1ProjectsProjectIdStagesStageVariantsGet: sdk.recipes,
}));

// The browser is asked to save the file, which a test only needs to see being asked
vi.mock('@/features/profiles/profileFile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/profiles/profileFile')>()),
  saveTextFile: files.save,
}));

// In the order the server lists them: by stage of the pipeline, then oldest first
const PROFILES = [
  profile('p3', { name: 'Plain split', stage: 'page-split', steps: [step('split.none')] }),
  profile('p1', {
    name: 'Clean flatbed scan',
    is_default: true,
    books: 7,
    steps: [step('geometry.deskew'), step('geometry.crop')],
  }),
  profile('p2', {
    name: 'Old book with plates',
    books: 1,
    steps: [
      step('geometry.deskew', { applies_to: 'text' }),
      step('geometry.deskew', { applies_to: 'pictures', enabled: false }),
    ],
  }),
];

const ITEMS = joinRows(
  [
    page('a', { position: 0 }),
    page('b', { position: 1 }),
    page('c', { position: 2, origin: 'placeholder' }),
    page('d', { position: 3 }),
  ],
  [row('a'), row('b'), row('d')],
);

const EMPTY_PAGE = { data: { items: [], total: 0, page: 1, size: 100, pages: 1 } };

function applied(name: string, missing: string[] = []): { data: unknown } {
  return {
    data: {
      recipe: recipe('made', { name, active: true }),
      missing_processors: missing,
      job: null,
    },
  };
}

describe('ProfileLibrary', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const chooseRecipe = vi.fn();

  const all = (id: string): HTMLElement[] => [
    ...document.body.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`),
  ];
  const byId = (id: string): HTMLElement | null => all(id)[0] ?? null;

  async function flush(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  async function render(
    options: { book?: boolean; selected?: string[]; dirty?: boolean } = {},
  ): Promise<void> {
    const book =
      options.book === false
        ? undefined
        : {
            processing: processing({ chooseRecipe, dirty: options.dirty ?? false }),
            items: ITEMS,
            selected: new Set(options.selected ?? []),
          };
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ProfileLibrary book={book} />
        </QueryClientProvider>,
      );
    });
    await flush();
  }

  async function click(element: HTMLElement | null): Promise<void> {
    await act(async () => {
      element?.click();
    });
    await flush();
  }

  const cardOf = (name: string): HTMLElement | undefined =>
    all('profile-row').find((card) => card.dataset.name === name);
  const inCard = (name: string, id: string): HTMLElement | null =>
    cardOf(name)?.querySelector<HTMLElement>(`[data-testid="${id}"]`) ?? null;

  async function choose(file: File): Promise<void> {
    const input = byId('profile-import-file') as HTMLInputElement;
    Object.defineProperty(input, 'files', { configurable: true, value: [file] });
    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    // The file is read before it is sent, which takes a turn of its own
    await flush();
    await flush();
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of [...Object.values(sdk), files.save, chooseRecipe]) {
      fake.mockReset();
    }
    sdk.list.mockResolvedValue(profilePage(PROFILES));
    sdk.processors.mockResolvedValue({
      data: {
        items: [
          deskew(),
          processor('geometry.crop', { title: 'Crop' }),
          processor('split.none', { title: 'Whole scan', stage: 'page-split' }),
        ],
        total: 3,
        page: 1,
        size: 100,
        pages: 1,
      },
    });
    sdk.jobs.mockResolvedValue(EMPTY_PAGE);
    sdk.recipes.mockResolvedValue(EMPTY_PAGE);
    sdk.apply.mockResolvedValue(applied('Clean flatbed scan'));
    sdk.duplicate.mockResolvedValue({ data: profile('p4', { name: 'Clean flatbed scan (copy)' }) });
    sdk.exporter.mockResolvedValue({
      data: {
        version: 1,
        stage: 'geometry',
        name: 'Clean flatbed scan',
        order: 'usual',
        steps: [],
      },
    });
    sdk.importer.mockResolvedValue({ data: profile('p5', { name: 'From a file' }) });
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

  describe('tabs and cards', () => {
    it('opens on the stage of the book, with a tab for each stage that has profiles and one for all', async () => {
      await render();

      expect(all('profile-tab').map((tab) => tab.dataset.tab)).toEqual([
        'page-split',
        'geometry',
        'all',
      ]);
      expect(
        all('profile-tab').find((tab) => tab.getAttribute('aria-selected') === 'true')?.dataset.tab,
      ).toBe('geometry');
      expect(all('profile-row').map((card) => card.dataset.name)).toEqual([
        'Clean flatbed scan',
        'Old book with plates',
      ]);
    });

    it('shows the books that use a profile and marks the default', async () => {
      await render();

      expect(inCard('Clean flatbed scan', 'profile-books')?.textContent).toBe('Used in 7 books');
      expect(inCard('Old book with plates', 'profile-books')?.textContent).toBe('Used in 1 book');
      expect(inCard('Clean flatbed scan', 'profile-default-badge')).not.toBeNull();
      expect(inCard('Old book with plates', 'profile-default-badge')).toBeNull();
    });

    it('names the steps by their titles, with the pages a step is limited to and the ones that are off', async () => {
      await render();

      expect(inCard('Clean flatbed scan', 'profile-steps')?.textContent).toBe('Deskew · Crop');
      expect(inCard('Old book with plates', 'profile-steps')?.textContent).toBe(
        'Deskew (text pages) · Deskew (pictures, off)',
      );
    });

    it('shows every stage under its own heading on the tab for all of them', async () => {
      await render();
      await click(all('profile-tab').find((tab) => tab.dataset.tab === 'all') ?? null);

      expect(all('profile-stage').map((section) => section.dataset.stage)).toEqual([
        'page-split',
        'geometry',
      ]);
      expect(all('profile-row')).toHaveLength(3);
    });

    it('says so when the stage of the book has no profile yet', async () => {
      sdk.list.mockResolvedValue(profilePage(PROFILES.slice(0, 1)));

      await render();

      expect(byId('profiles-empty')?.textContent).toContain('no Geometry profile');
      expect(all('profile-row')).toHaveLength(0);
    });

    it('starts on all stages and leaves out what needs a book when it is opened from the account', async () => {
      await render({ book: false });

      expect(
        all('profile-tab').find((tab) => tab.getAttribute('aria-selected') === 'true')?.dataset.tab,
      ).toBe('all');
      expect(all('profile-apply-book')).toHaveLength(0);
      expect(all('profile-apply-pages')).toHaveLength(0);
      expect(byId('profile-from-book')).toBeNull();
      expect(byId('profile-import')).not.toBeNull();
      expect(sdk.jobs).not.toHaveBeenCalled();
    });
  });

  describe('applying to the book', () => {
    it('makes the profile the active recipe and opens it in the panel', async () => {
      await render();
      await click(inCard('Clean flatbed scan', 'profile-apply-book'));

      expect(sdk.apply).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { project_id: 'project', profile_id: 'p1' },
          body: { activate: true },
        }),
      );
      expect(chooseRecipe).toHaveBeenCalledWith('made');
      expect(inCard('Clean flatbed scan', 'profile-notice')?.textContent).toContain(
        'Applied the profile “Clean flatbed scan”. It is the active recipe of this book now.',
      );
    });

    it('adds the profile as a variant only, when the choice to make it the active recipe is off', async () => {
      sdk.apply.mockResolvedValue({
        data: {
          recipe: recipe('made', { name: 'Clean flatbed scan', active: false }),
          missing_processors: [],
          job: null,
        },
      });

      await render();
      expect(byId('profile-activate')).toHaveProperty('checked', true);
      await click(byId('profile-activate'));
      await click(inCard('Clean flatbed scan', 'profile-apply-book'));

      expect(sdk.apply).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { project_id: 'project', profile_id: 'p1' },
          body: { activate: false },
        }),
      );
      expect(inCard('Clean flatbed scan', 'profile-notice')?.textContent).toContain(
        'as a variant of this book',
      );
    });

    it('names the steps that were left out because their processor is not installed', async () => {
      sdk.apply.mockResolvedValue(applied('Clean flatbed scan', ['geometry.gone']));

      await render();
      await click(inCard('Clean flatbed scan', 'profile-apply-book'));

      expect(inCard('Clean flatbed scan', 'profile-left-out')?.textContent).toContain(
        'geometry.gone',
      );
    });

    it('does not open the recipe of another stage in the panel', async () => {
      await render();
      await click(all('profile-tab').find((tab) => tab.dataset.tab === 'page-split') ?? null);
      await click(inCard('Plain split', 'profile-apply-book'));

      expect(sdk.apply).toHaveBeenCalledOnce();
      expect(chooseRecipe).not.toHaveBeenCalled();
    });

    it('waits for the unsaved changes of the recipe to be saved or reverted', async () => {
      await render({ dirty: true });

      expect(inCard('Clean flatbed scan', 'profile-apply-book')).toHaveProperty('disabled', true);
      expect(inCard('Clean flatbed scan', 'profile-apply-pages')).toHaveProperty('disabled', true);
    });

    it('shows the answer of the server when the profile cannot be applied', async () => {
      sdk.apply.mockRejectedValue(parseProblem({ detail: 'No step can run' }, 422));

      await render();
      await click(inCard('Clean flatbed scan', 'profile-apply-book'));

      expect(cardOf('Clean flatbed scan')?.textContent).toContain('No step can run');
      expect(chooseRecipe).not.toHaveBeenCalled();
    });
  });

  describe('applying to the selected pages', () => {
    it('is not offered until pages are selected', async () => {
      await render();

      expect(inCard('Clean flatbed scan', 'profile-apply-pages')).toHaveProperty('disabled', true);
      expect(inCard('Clean flatbed scan', 'profile-apply-pages')?.title).toBe(
        'Select pages in the grid first',
      );
    });

    it('pins the variant to the selected pages that have an image, and leaves the active recipe', async () => {
      sdk.apply.mockResolvedValue({
        data: {
          recipe: recipe('made', { name: 'Clean flatbed scan', active: false }),
          missing_processors: [],
          job: { id: 'job' },
        },
      });

      await render({ selected: ['d', 'c', 'a'] });
      await click(inCard('Clean flatbed scan', 'profile-apply-pages'));

      expect(sdk.apply).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { project_id: 'project', profile_id: 'p1' },
          body: { activate: false, page_ids: ['a', 'd'] },
        }),
      );
      expect(inCard('Clean flatbed scan', 'profile-notice')?.textContent).toContain(
        'Applied the profile “Clean flatbed scan” to 2 pages. The stage is running on them.',
      );
    });

    it('waits while another job of the book is running', async () => {
      sdk.jobs.mockResolvedValue({
        data: { items: [{ id: 'busy' }], total: 1, page: 1, size: 20, pages: 1 },
      });

      await render({ selected: ['a'] });

      // The running jobs come with their own query, which may answer after the first render
      await vi.waitFor(() =>
        expect(inCard('Clean flatbed scan', 'profile-apply-pages')).toHaveProperty('disabled', true),
      );
      expect(inCard('Clean flatbed scan', 'profile-apply-pages')?.title).toBe(
        'The book is busy with another job',
      );
    });
  });

  describe('copying and exchanging', () => {
    it('duplicates a profile through the server and reads the list again', async () => {
      await render();
      await click(inCard('Clean flatbed scan', 'profile-duplicate'));

      expect(sdk.duplicate).toHaveBeenCalledWith(
        expect.objectContaining({ path: { profile_id: 'p1' } }),
      );
      expect(sdk.list).toHaveBeenCalledTimes(2);
      expect(inCard('Clean flatbed scan', 'profile-notice')?.textContent).toBe(
        'Saved the copy “Clean flatbed scan (copy)”.',
      );
    });

    it('saves a profile to a file named after it, with the file the server wrote', async () => {
      await render();
      await click(inCard('Clean flatbed scan', 'profile-export'));

      expect(sdk.exporter).toHaveBeenCalledWith(
        expect.objectContaining({ path: { profile_id: 'p1' } }),
      );
      expect(files.save).toHaveBeenCalledWith(
        'clean-flatbed-scan.bookreviver-profile.json',
        expect.stringContaining('"version": 1'),
      );
    });

    it('reads a chosen file, sends it as it is, and reads the list again', async () => {
      const file = { version: 1, stage: 'geometry', name: 'From a file', steps: [] };

      await render();
      await choose(new File([JSON.stringify(file)], 'profile.json'));

      expect(sdk.importer).toHaveBeenCalledWith(expect.objectContaining({ body: file }));
      expect(sdk.list).toHaveBeenCalledTimes(2);
      expect(byId('profile-library-notice')?.textContent).toBe(
        'Imported the profile “From a file”.',
      );
    });

    it('turns away a file that is not a profile without a request', async () => {
      await render();
      await choose(new File(['not json'], 'profile.json'));

      expect(sdk.importer).not.toHaveBeenCalled();
      expect(byId('profile-import-problem')?.textContent).toContain('does not hold JSON');
    });

    it('turns away a file of a version it does not know without a request', async () => {
      await render();
      await choose(
        new File(
          [JSON.stringify({ version: 9, stage: 'geometry', name: 'N', steps: [] })],
          'p.json',
        ),
      );

      expect(sdk.importer).not.toHaveBeenCalled();
      expect(byId('profile-import-problem')?.textContent).toContain('version');
    });

    it('shows what the server found wrong with a file, such as a processor that is not installed', async () => {
      sdk.importer.mockRejectedValue(
        parseProblem(
          {
            detail: 'The profile file needs processors that are not installed here: geometry.gone.',
          },
          422,
        ),
      );

      await render();
      await choose(
        new File(
          [
            JSON.stringify({
              version: 1,
              stage: 'geometry',
              name: 'N',
              steps: [{ processor_key: 'geometry.gone' }],
            }),
          ],
          'p.json',
        ),
      );

      expect(byId('profile-import-error')?.textContent).toContain('geometry.gone');
      expect(byId('profile-library-notice')).toBeNull();
    });

    it('opens the dialog that keeps the steps of the book as a new profile', async () => {
      await render();
      await click(byId('profile-from-book'));

      expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
      expect(byId('profile-save-submit')).not.toBeNull();
    });
  });

  describe('the side panel', () => {
    it('holds the library of the book while it is open, and nothing while it is shut', async () => {
      const book = { processing: processing(), items: ITEMS, selected: new Set<string>() };
      const draw = async (open: boolean): Promise<void> => {
        await act(async () => {
          root.render(
            <QueryClientProvider client={client}>
              <ProfileLibraryPanel open={open} onOpenChange={() => undefined} book={book} />
            </QueryClientProvider>,
          );
        });
        await flush();
      };

      await draw(false);
      expect(byId('profile-library-panel')).toBeNull();
      await draw(true);

      expect(byId('profile-library-panel')?.textContent).toContain('Profiles');
      expect(all('profile-row')).toHaveLength(2);
    });
  });
});
