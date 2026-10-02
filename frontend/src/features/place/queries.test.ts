import { QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { BookPlaceBody, BookPlaceSchema } from '@/api';
import { bodyOf, placeOfBody } from '@/features/place/address';
import { placeOptions, placeWritesSettled, sendPlace } from '@/features/place/queries';
import { HttpStatus } from '@/shared/http/status';

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));

vi.mock('@/api', () => ({
  getPlaceApiV1ProjectsProjectIdPlaceGet: api.get,
  putPlaceApiV1ProjectsProjectIdPlacePut: api.put,
}));

vi.mock('@/api/@tanstack/react-query.gen', () => ({
  getPlaceApiV1ProjectsProjectIdPlaceGetQueryKey: (options: { path: { project_id: string } }) => [
    'place',
    options.path.project_id,
  ],
}));

const BOOK = 'book-1';
const BODY: BookPlaceBody = bodyOf(
  { mode: 'workspace', stage: 'geometry', page: 'p-1' },
  null,
  null,
);
const PLACE: BookPlaceSchema = placeOfBody(BODY, new Date('2026-10-02T10:00:00Z'));

describe('placeOptions', () => {
  beforeEach(() => {
    api.get.mockReset();
    api.put.mockReset();
  });

  it('reads the place of a book', async () => {
    api.get.mockResolvedValue({ data: PLACE, response: { status: 200 } });
    const place = await new QueryClient().fetchQuery(placeOptions(BOOK));
    expect(place).toEqual(PLACE);
    expect(api.get).toHaveBeenCalledWith(expect.objectContaining({ path: { project_id: BOOK } }));
  });

  it('reads no place for a book the account has not worked on', async () => {
    api.get.mockResolvedValue({ data: {}, response: { status: HttpStatus.NoContent } });
    expect(await new QueryClient().fetchQuery(placeOptions(BOOK))).toBeNull();
  });

  it('waits for the writes that are on their way before it reads', async () => {
    const order: string[] = [];
    let answer: () => void = () => undefined;
    api.put.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          answer = () => {
            order.push('written');
            resolve();
          };
        }),
    );
    api.get.mockImplementation(() => {
      order.push('read');
      return Promise.resolve({ data: PLACE, response: { status: 200 } });
    });

    void sendPlace(BOOK, BODY, false);
    const read = new QueryClient().fetchQuery(placeOptions(BOOK));
    await Promise.resolve();
    expect(order).toEqual([]);

    answer();
    await read;
    expect(order).toEqual(['written', 'read']);
  });
});

describe('sendPlace', () => {
  beforeEach(() => {
    api.put.mockReset();
  });

  it('sends the body, with keepalive when the page is closing', async () => {
    api.put.mockResolvedValue({ data: PLACE });
    await sendPlace(BOOK, BODY, true);
    expect(api.put).toHaveBeenCalledWith({
      path: { project_id: BOOK },
      body: BODY,
      keepalive: true,
      throwOnError: true,
    });
  });

  it('is settled when a write failed, and fails for its own caller', async () => {
    api.put.mockRejectedValue(new Error('offline'));
    await expect(sendPlace(BOOK, BODY, false)).rejects.toThrow('offline');
    await expect(placeWritesSettled(BOOK)).resolves.toBeUndefined();
  });
});
