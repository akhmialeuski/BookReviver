import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { LogOutIcon } from 'lucide-react';
import { authCookieLogoutApiV1AuthLogoutPostMutation } from '@/api/@tanstack/react-query.gen';
import { useSession } from '@/features/auth/session';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The bar above every screen of the signed-in area: the application name, who is signed in, and the way out.
 */

export function AppHeader(): React.JSX.Element {
  const queryClient = useQueryClient();
  const router = useRouter();
  const session = useSession();
  const signOut = useMutation({
    ...authCookieLogoutApiV1AuthLogoutPostMutation(),
    // A 401 here means the session was already gone, which is the state signing out wants
    onSettled: async () => {
      queryClient.clear();
      await router.navigate({ to: '/sign-in' });
    },
  });

  return (
    <header className="border-b">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4">
        <Link to="/projects" className="text-lg font-semibold tracking-tight">
          {MESSAGES.app.name}
        </Link>
        <div className="flex items-center gap-3">
          <span className="hidden text-sm text-muted-foreground sm:inline">
            {session.data?.email}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={signOut.isPending}
            onClick={() => signOut.mutate({})}
          >
            <LogOutIcon />
            {signOut.isPending ? MESSAGES.auth.signingOut : MESSAGES.auth.signOut}
          </Button>
        </div>
      </div>
    </header>
  );
}
