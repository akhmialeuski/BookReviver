import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processor, step } from '@/features/processing/fixtures';
import { profile, profilePage } from '@/features/profiles/fixtures';
import { ProfilesPage } from '@/features/profiles/ProfilesPage';

/**
 * The profiles of the account in the settings, which are the library without a book: grouped by stage, with the steps
 * each holds.
 */

const sdk = vi.hoisted(() => ({
  list: vi.fn(),
  processors: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listProfilesApiV1RecipeProfilesGet: sdk.list,
  listProcessorsApiV1ProcessorsGet: sdk.processors,
}));

// In the order the server lists them: by stage of the pipeline, then oldest first
const PROFILES = [
  profile('p3', {
    name: 'Plain split',
    stage: 'page-split',
    steps: [step('split.none')],
  }),
  profile('p1', { name: 'Clean flatbed scan' }),
  profile('p2', { name: 'Photographed book' }),
];

describe('ProfilesPage', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  const byId = (id: string): HTMLElement[] => [
    ...container.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`),
  ];

  async function render(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ProfilesPage />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
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

  it('groups the profiles by stage in the order the server lists them', async () => {
    await render();

    expect(byId('profile-stage').map((section) => section.dataset.stage)).toEqual([
      'page-split',
      'geometry',
    ]);
    expect(
      byId('profile-row').map((row) => [row.dataset.name, row.closest('section')?.dataset.stage]),
    ).toEqual([
      ['Plain split', 'page-split'],
      ['Clean flatbed scan', 'geometry'],
      ['Photographed book', 'geometry'],
    ]);
  });

  it('names the steps of a profile by their titles and marks the ones that are off', async () => {
    await render();

    expect(byId('profile-steps')[1]?.textContent).toBe('Deskew · Crop (off)');
    expect(byId('profile-steps')[0]?.textContent).toBe('Whole scan');
  });

  it('is the library without a book: it counts the books of a profile and offers no application', async () => {
    sdk.list.mockResolvedValue(profilePage([profile('p1', { books: 3 })]));

    await render();

    expect(byId('profile-books')[0]?.textContent).toBe('Used in 3 books');
    expect(byId('profile-duplicate')).toHaveLength(1);
    expect(byId('profile-export')).toHaveLength(1);
    expect(byId('profile-import')).toHaveLength(1);
    expect(byId('profile-apply-book')).toHaveLength(0);
    expect(byId('profile-apply-pages')).toHaveLength(0);
  });

  it('says so when the account has no profile yet', async () => {
    sdk.list.mockResolvedValue(profilePage([]));

    await render();

    expect(byId('profiles-empty')).toHaveLength(1);
    expect(byId('profile-row')).toHaveLength(0);
  });
});
