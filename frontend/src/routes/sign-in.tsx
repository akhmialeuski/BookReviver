import { createFileRoute, redirect } from '@tanstack/react-router';
import { type OAuthFailure, parseFailure } from '@/features/auth/oauth';
import { safeRedirect } from '@/features/auth/redirect';
import { SignInForm } from '@/features/auth/SignInForm';
import { fetchSession } from '@/features/auth/session';

/**
 * The sign-in page. Someone who is already signed in is sent on at once, and the `redirect` search parameter says
 * where, after the check that it stays inside the application. The `oauthError` parameter carries the name of a failed
 * sign-in with a provider, which the form turns into its message; any other value is dropped.
 */

export const Route = createFileRoute('/sign-in')({
  validateSearch: (
    search: Record<string, unknown>,
  ): { redirect?: string; oauthError?: OAuthFailure } => {
    const oauthError = parseFailure(search.oauthError);
    return {
      ...(typeof search.redirect === 'string' ? { redirect: safeRedirect(search.redirect) } : {}),
      ...(oauthError === undefined ? {} : { oauthError }),
    };
  },
  beforeLoad: async ({ context, search }) => {
    if ((await fetchSession(context.queryClient)) !== null) {
      throw redirect({ href: safeRedirect(search.redirect) });
    }
  },
  component: SignInPage,
});

function SignInPage(): React.JSX.Element {
  const search = Route.useSearch();
  return <SignInForm redirectTo={safeRedirect(search.redirect)} oauthError={search.oauthError} />;
}
