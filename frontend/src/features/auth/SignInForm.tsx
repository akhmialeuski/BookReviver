import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { useState } from 'react';
import {
  authCookieLoginApiV1AuthLoginPostMutation,
  verifyRequestTokenApiV1AuthRequestVerifyTokenPostMutation,
} from '@/api/@tanstack/react-query.gen';
import { AuthCard } from '@/features/auth/AuthCard';
import { ProblemCode } from '@/shared/http/codes';
import { describeError, ProblemError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Alert, AlertDescription } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextField } from '@/shared/ui/text-field';

/**
 * The sign-in form. A successful sign-in sets the session cookie, and the page then goes to the address the visitor
 * was sent from. An account whose address is not confirmed can ask for a new confirmation link right here.
 */

export function SignInForm({ redirectTo }: { redirectTo: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const signIn = useMutation({
    ...authCookieLoginApiV1AuthLoginPostMutation(),
    onSuccess: async () => {
      // A session of someone else may still be cached from before, so nothing of it is kept
      await queryClient.invalidateQueries();
      router.history.push(redirectTo);
    },
  });
  const resend = useMutation(verifyRequestTokenApiV1AuthRequestVerifyTokenPostMutation());

  const unverified =
    signIn.error instanceof ProblemError && signIn.error.code === ProblemCode.LoginUserNotVerified;

  return (
    <AuthCard title={MESSAGES.auth.signIn.title} description={MESSAGES.auth.signIn.description}>
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          signIn.mutate({ body: { username: email, password } });
        }}
      >
        <TextField
          label={MESSAGES.auth.fields.email}
          name="username"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <TextField
          label={MESSAGES.auth.fields.password}
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {signIn.isError ? <ErrorAlert message={describeError(signIn.error)} /> : null}
        {unverified ? (
          <Button
            type="button"
            variant="outline"
            disabled={resend.isPending || resend.isSuccess}
            onClick={() => resend.mutate({ body: { email } })}
          >
            {MESSAGES.auth.signIn.resend}
          </Button>
        ) : null}
        {resend.isSuccess ? (
          <Alert>
            <AlertDescription>{MESSAGES.auth.signIn.resent}</AlertDescription>
          </Alert>
        ) : null}
        {resend.isError ? <ErrorAlert message={describeError(resend.error)} /> : null}
        <Button type="submit" disabled={signIn.isPending || signIn.isSuccess}>
          {signIn.isPending ? MESSAGES.auth.signIn.submitting : MESSAGES.auth.signIn.submit}
        </Button>
      </form>
      <p className="text-center text-sm text-muted-foreground">
        {MESSAGES.auth.signIn.noAccount}{' '}
        <Link to="/register" className="text-foreground underline underline-offset-4">
          {MESSAGES.auth.signIn.register}
        </Link>
      </p>
    </AuthCard>
  );
}
