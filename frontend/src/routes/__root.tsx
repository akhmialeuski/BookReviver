import { createRootRouteWithContext, Link, Outlet } from '@tanstack/react-router';
import type { RouterContext } from '@/app/router';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The root route: the outlet every screen renders in, and the page shown for an address that matches no route.
 */

function NotFound(): React.JSX.Element {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 p-6">
      <p>{MESSAGES.app.notFound}</p>
      <Button asChild>
        <Link to="/">{MESSAGES.app.backHome}</Link>
      </Button>
    </main>
  );
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: NotFound,
});
