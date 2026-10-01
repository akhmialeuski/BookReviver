import { matchQuery, type QueryClient, useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useEffect } from 'react';
import { isUnauthorized, sessionQuery } from '@/features/auth/session';

/**
 * Sends the visitor back to the sign-in page when the server stops accepting the session.
 *
 * A session can end while a page is open, by expiry or by signing out in another tab. The first request that the
 * server answers with 401 then drops the cached account and makes the router run its guards again, and the guard of
 * the signed-in area redirects to the sign-in page with the current address to come back to.
 *
 * The session query is left out on purpose. Its own 401 is the answer "nobody is signed in", the guard already acts
 * on it, and reacting to it here would drop the query that a mounted header observes, which fetches it again, which
 * fails again, without end.
 */

/**
 * Call `onUnauthorized` whenever a query other than the session, or any mutation, fails with a 401.
 *
 * @returns A function that stops watching.
 */
export function watchForUnauthorized(
  queryClient: QueryClient,
  onUnauthorized: () => void,
): () => void {
  const sessionFilter = { queryKey: sessionQuery().queryKey };
  const stopQueries = queryClient.getQueryCache().subscribe((event) => {
    if (
      event.type === 'updated' &&
      event.action.type === 'error' &&
      isUnauthorized(event.action.error) &&
      !matchQuery(sessionFilter, event.query)
    ) {
      onUnauthorized();
    }
  });
  const stopMutations = queryClient.getMutationCache().subscribe((event) => {
    if (
      event.type === 'updated' &&
      event.action.type === 'error' &&
      isUnauthorized(event.action.error)
    ) {
      onUnauthorized();
    }
  });
  return () => {
    stopQueries();
    stopMutations();
  };
}

/** Watch the queries and mutations of the client while the signed-in area is shown. */
export function useRedirectOnUnauthorized(): void {
  const queryClient = useQueryClient();
  const router = useRouter();

  useEffect(
    () =>
      watchForUnauthorized(queryClient, () => {
        queryClient.removeQueries({ queryKey: sessionQuery().queryKey });
        void router.invalidate();
      }),
    [queryClient, router],
  );
}
