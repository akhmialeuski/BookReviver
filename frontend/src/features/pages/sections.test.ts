import { QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SECTIONS_PAGE_SIZE, sectionsOptions } from '@/features/pages/sections';
import { section } from '@/features/workspace/fixtures';

/** The sections of a book read as one list, whatever number of requests the server needs to send them. */

const sdk = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGet: sdk.list,
}));

const PROJECT = 'book';

describe('sectionsOptions', () => {
  beforeEach(() => {
    sdk.list.mockReset();
  });

  it('reads every request of the sections and keeps them as one list in the order they came', async () => {
    sdk.list
      .mockResolvedValueOnce({
        data: {
          items: [section('a', 'p0')],
          total: 2,
          page: 1,
          size: SECTIONS_PAGE_SIZE,
          pages: 2,
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [section('b', 'p1')],
          total: 2,
          page: 2,
          size: SECTIONS_PAGE_SIZE,
          pages: 2,
        },
      });

    const sections = await new QueryClient().fetchQuery(sectionsOptions(PROJECT));

    expect(sections.map((entry) => entry.id)).toEqual(['a', 'b']);
    expect(sdk.list).toHaveBeenCalledTimes(2);
    expect(sdk.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        path: { project_id: PROJECT },
        query: { page: 2, size: SECTIONS_PAGE_SIZE },
      }),
    );
  });

  it('gives no sections for a book that has none', async () => {
    sdk.list.mockResolvedValue({
      data: { items: [], total: 0, page: 1, size: SECTIONS_PAGE_SIZE, pages: 0 },
    });

    expect(await new QueryClient().fetchQuery(sectionsOptions(PROJECT))).toEqual([]);
  });
});
