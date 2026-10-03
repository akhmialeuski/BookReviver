import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useMatch, useRouter } from '@tanstack/react-router';
import { BookmarkIcon, LogOutIcon } from 'lucide-react';
import { authCookieLogoutApiV1AuthLogoutPostMutation } from '@/api/@tanstack/react-query.gen';
import { useSession } from '@/features/auth/session';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The bar above the screens of the signed-in area: the application name, who is signed in, and the way out.
 *
 * The screens of a book have a header of their own, which holds the account menu defined here, so this bar leaves
 * the page when a book is open.
 */

const INITIALS_LENGTH = 2;

function useSignOut() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useMutation({
    ...authCookieLogoutApiV1AuthLogoutPostMutation(),
    // A 401 here means the session was already gone, which is the state signing out wants
    onSettled: async () => {
      queryClient.clear();
      await router.navigate({ to: '/sign-in' });
    },
  });
}

/** The round button with the initials of the signed-in account, which opens its email and the way out. */
export function AccountMenu(): React.JSX.Element {
  const session = useSession();
  const signOut = useSignOut();
  const email = session.data?.email ?? '';

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="secondary"
          size="icon-sm"
          className="rounded-full text-xs"
          aria-label={MESSAGES.workspace.header.accountMenu}
        >
          {email.slice(0, INITIALS_LENGTH).toUpperCase()}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel className="font-normal text-muted-foreground">{email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/settings/profiles" data-testid="account-profiles">
            <BookmarkIcon />
            {MESSAGES.profiles.menu}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem disabled={signOut.isPending} onSelect={() => signOut.mutate({})}>
          <LogOutIcon />
          {signOut.isPending ? MESSAGES.auth.signingOut : MESSAGES.auth.signOut}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppHeader(): React.JSX.Element | null {
  const session = useSession();
  const signOut = useSignOut();
  const inBook = useMatch({ from: '/_authenticated/projects/$projectId', shouldThrow: false });

  if (inBook !== undefined) {
    return null;
  }

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
          <Button variant="ghost" size="sm" asChild>
            <Link to="/settings/profiles" data-testid="library-profiles">
              <BookmarkIcon />
              {MESSAGES.profiles.menu}
            </Link>
          </Button>
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
