import { type QueryClient, queryOptions } from '@tanstack/react-query';
import {
  type BookPlaceBody,
  type BookPlaceSchema,
  getPlaceApiV1BooksBookIdPlaceGet,
  putPlaceApiV1BooksBookIdPlacePut,
} from '@/api';
import { getPlaceApiV1BooksBookIdPlaceGetQueryKey } from '@/api/@tanstack/react-query.gen';
import { HttpStatus } from '@/shared/http/status';

/**
 * The place of a book as one query, and the write that replaces it.
 *
 * The query holds the place the reader left the book at, or null when they have not worked on it. The writer
 * (`writer.ts`) also puts the place it is about to write into this cache, so a screen that opens right after another
 * reads where the reader is, and not where the server last heard of them. A read waits for the writes that are still
 * on their way, for the same reason: a book opened a moment after it was closed must open where it was closed.
 */

/** The writes of each book that the server has not answered yet. */
const writesInFlight = new Map<string, Set<Promise<void>>>();

/** The key of the place of a book in the query cache. */
export function placeKey(bookId: string) {
  return getPlaceApiV1BooksBookIdPlaceGetQueryKey({ path: { book_id: bookId } });
}

/** Wait until every write of the place of a book that was sent has been answered, whether it succeeded or not. */
export async function placeWritesSettled(bookId: string): Promise<void> {
  await Promise.allSettled([...(writesInFlight.get(bookId) ?? [])]);
}

/**
 * Write the place of a book.
 *
 * @param projectId The book.
 * @param body Where the reader is.
 * @param keepalive Whether the browser may finish the request after the page is gone, for the write on closing a tab.
 */
export function sendPlace(bookId: string, body: BookPlaceBody, keepalive: boolean): Promise<void> {
  const request = putPlaceApiV1BooksBookIdPlacePut({
    path: { book_id: bookId },
    body,
    keepalive,
    throwOnError: true,
  }).then(() => undefined);
  const pending = writesInFlight.get(bookId) ?? new Set<Promise<void>>();
  pending.add(request);
  writesInFlight.set(bookId, pending);
  const forget = (): void => {
    pending.delete(request);
  };
  request.then(forget, forget);
  return request;
}

/** Query options of the place of a book, which resolve to null for a book the account has not worked on. */
export function placeOptions(bookId: string) {
  return queryOptions({
    queryKey: placeKey(bookId),
    queryFn: async ({ signal }): Promise<BookPlaceSchema | null> => {
      await placeWritesSettled(bookId);
      const { data, response } = await getPlaceApiV1BooksBookIdPlaceGet({
        path: { book_id: bookId },
        signal,
        throwOnError: true,
      });
      return response.status === HttpStatus.NoContent || typeof data !== 'object' ? null : data;
    },
  });
}

/** Read the place of a book that is in the cache, or null when there is none. */
export function cachedPlace(queryClient: QueryClient, bookId: string): BookPlaceSchema | null {
  return queryClient.getQueryData(placeOptions(bookId).queryKey) ?? null;
}

/** Put a place into the cache, as the writer does for the place it is writing. */
export function cachePlace(queryClient: QueryClient, bookId: string, place: BookPlaceSchema): void {
  queryClient.setQueryData(placeOptions(bookId).queryKey, place);
}
