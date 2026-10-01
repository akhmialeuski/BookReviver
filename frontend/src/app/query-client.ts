import { QueryClient } from '@tanstack/react-query';

/**
 * The one TanStack Query client of the application.
 *
 * Server state lives here. Data stays fresh for a short while so moving between screens does not refetch at once,
 * and the project event stream patches or invalidates the affected queries when the server changes something.
 */

const STALE_TIME_MS = 15_000;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: STALE_TIME_MS, refetchOnWindowFocus: false },
  },
});
