import { QueryClient } from '@tanstack/react-query';
import { shouldRetry } from '@/shared/http/retry';

/**
 * The one TanStack Query client of the application.
 *
 * Server state lives here. Data stays fresh for a short while so moving between screens does not refetch at once,
 * and the project event stream patches or invalidates the affected queries when the server changes something.
 * A failed query is tried again only when the failure can pass, so an ended session or a missing book is shown at
 * once and not after the retries.
 */

const STALE_TIME_MS = 15_000;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: STALE_TIME_MS, refetchOnWindowFocus: false, retry: shouldRetry },
  },
});
