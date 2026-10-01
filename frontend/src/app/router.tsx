import type { QueryClient } from '@tanstack/react-query';
import { createRouter } from '@tanstack/react-router';
import { queryClient } from '@/app/query-client';
import { routeTree } from '@/routeTree.gen';

/**
 * The application router, built from the file-based route tree.
 *
 * The query client travels in the router context, so a route's `beforeLoad` can read the session through it.
 */

export interface RouterContext {
  queryClient: QueryClient;
}

export const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: 'intent',
  scrollRestoration: true,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
