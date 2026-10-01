import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useEffect } from 'react';
import { isUnauthorized, sessionQuery } from '@/features/auth/session';

/**
 * Sends the visitor back to the sign-in page when the server stops accepting the session.
 *
 * A session can end while a page is open, by expiry or by signing out in another tab. The first request that the
 * server answers with 401 then drops the cached account and makes the router run its guards again, and the guard of
 * the signed-in area redirects to the sign-in page with the current address to come back to.
 */

/** Watch every query and mutation of the client and react to the first 401 of one that is not the session query. */
export function useRedirectOnUnauthorized(): void {
  const queryClient = useQueryClient();
  const router = useRouter();

  useEffect(() => {
    const onUnauthorized = (): void => {
      queryClient.removeQueries({ queryKey: sessionQuery().queryKey });
      void router.invalidate();
    };
    const stopQueries = queryClient.getQueryCache().subscribe((event) => {
      if (
        event.type === 'updated' &&
        event.action.type === 'error' &&
        isUnauthorized(event.action.error)
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
  }, [queryClient, router]);
}
