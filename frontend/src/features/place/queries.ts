import { type QueryClient, queryOptions } from '@tanstack/react-query';
import {
  type BookPlaceBody,
  type BookPlaceSchema,
  getPlaceApiV1ProjectsProjectIdPlaceGet,
  putPlaceApiV1ProjectsProjectIdPlacePut,
} from '@/api';
import { getPlaceApiV1ProjectsProjectIdPlaceGetQueryKey } from '@/api/@tanstack/react-query.gen';
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
export function placeKey(projectId: string) {
  return getPlaceApiV1ProjectsProjectIdPlaceGetQueryKey({ path: { project_id: projectId } });
}

/** Wait until every write of the place of a book that was sent has been answered, whether it succeeded or not. */
export async function placeWritesSettled(projectId: string): Promise<void> {
  await Promise.allSettled([...(writesInFlight.get(projectId) ?? [])]);
}

/**
 * Write the place of a book.
 *
 * @param projectId The book.
 * @param body Where the reader is.
 * @param keepalive Whether the browser may finish the request after the page is gone, for the write on closing a tab.
 */
export function sendPlace(
  projectId: string,
  body: BookPlaceBody,
  keepalive: boolean,
): Promise<void> {
  const request = putPlaceApiV1ProjectsProjectIdPlacePut({
    path: { project_id: projectId },
    body,
    keepalive,
    throwOnError: true,
  }).then(() => undefined);
  const pending = writesInFlight.get(projectId) ?? new Set<Promise<void>>();
  pending.add(request);
  writesInFlight.set(projectId, pending);
  const forget = (): void => {
    pending.delete(request);
  };
  request.then(forget, forget);
  return request;
}

/** Query options of the place of a book, which resolve to null for a book the account has not worked on. */
export function placeOptions(projectId: string) {
  return queryOptions({
    queryKey: placeKey(projectId),
    queryFn: async ({ signal }): Promise<BookPlaceSchema | null> => {
      await placeWritesSettled(projectId);
      const { data, response } = await getPlaceApiV1ProjectsProjectIdPlaceGet({
        path: { project_id: projectId },
        signal,
        throwOnError: true,
      });
      return response.status === HttpStatus.NoContent || typeof data !== 'object' ? null : data;
    },
  });
}

/** Read the place of a book that is in the cache, or null when there is none. */
export function cachedPlace(queryClient: QueryClient, projectId: string): BookPlaceSchema | null {
  return queryClient.getQueryData(placeOptions(projectId).queryKey) ?? null;
}

/** Put a place into the cache, as the writer does for the place it is writing. */
export function cachePlace(
  queryClient: QueryClient,
  projectId: string,
  place: BookPlaceSchema,
): void {
  queryClient.setQueryData(placeOptions(projectId).queryKey, place);
}
