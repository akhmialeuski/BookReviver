import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useEffect, useRef } from 'react';
import { AuthCard } from '@/features/auth/AuthCard';
import { type CallbackParams, failureOf, OAuthFailure } from '@/features/auth/oauth';
import { completeSignIn } from '@/features/auth/providers';
import { DEFAULT_LANDING } from '@/features/auth/redirect';
import { ProblemError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';

/**
 * The page a provider returns the browser to, `/auth/{provider}/callback`.
 *
 * It hands the `code` and the `state` of the address to the server's callback route with this browser's cookies,
 * which is what lets the server compare the state with the cookie it set when the sign-in began. A success goes on
 * to the book list, and any failure goes back to the sign-in screen, which says what happened.
 */

export function OAuthCallback({
  provider,
  params,
}: {
  provider: string;
  params: CallbackParams;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const router = useRouter();
  // A code works once, and React runs an effect twice in development, so the request is guarded
  const started = useRef(false);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;

    async function finish(): Promise<void> {
      if (params.kind === 'refused') {
        await router.navigate({
          to: '/sign-in',
          search: { oauthError: OAuthFailure.Refused },
          replace: true,
        });
        return;
      }
      try {
        await completeSignIn(provider, params.code, params.state);
      } catch (error) {
        const code = error instanceof ProblemError ? error.code : null;
        await router.navigate({
          to: '/sign-in',
          search: { oauthError: failureOf(code) },
          replace: true,
        });
        return;
      }
      // A session of someone else may still be cached from before, so nothing of it is kept
      await queryClient.invalidateQueries();
      router.history.replace(DEFAULT_LANDING);
    }

    void finish();
  }, [provider, params, queryClient, router]);

  return (
    <AuthCard title={MESSAGES.auth.providers.callback.title}>
      <p className="text-sm text-muted-foreground">{MESSAGES.auth.providers.callback.pending}</p>
    </AuthCard>
  );
}
