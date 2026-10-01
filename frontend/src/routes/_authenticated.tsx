import { createFileRoute, Outlet, redirect } from '@tanstack/react-router';
import { AppHeader } from '@/features/auth/AppHeader';
import { fetchSession } from '@/features/auth/session';
import { useRedirectOnUnauthorized } from '@/features/auth/unauthorized';

/**
 * The guard of the signed-in area. Every route under it asks the server who is signed in before it loads, and a
 * visitor is sent to the sign-in page with the address they wanted, to return to it afterwards.
 */

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: async ({ context, location }) => {
    if ((await fetchSession(context.queryClient)) === null) {
      throw redirect({ to: '/sign-in', search: { redirect: location.href } });
    }
  },
  component: AuthenticatedLayout,
});

function AuthenticatedLayout(): React.JSX.Element {
  useRedirectOnUnauthorized();
  return (
    <>
      <AppHeader />
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <Outlet />
      </main>
    </>
  );
}
