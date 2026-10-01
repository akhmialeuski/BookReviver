import { useMutation } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { resetResetPasswordApiV1AuthResetPasswordPostMutation } from '@/api/@tanstack/react-query.gen';
import { AuthCard } from '@/features/auth/AuthCard';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button, buttonVariants } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextField } from '@/shared/ui/text-field';

/**
 * The page the reset link of the mail opens, `/reset-password?token=...`. It asks for the new password, sends it
 * with the token of the link, and after the server accepts it points to the sign-in screen. A link without a
 * token, or one the server refuses, offers to ask for a new mail.
 */

export function ResetPasswordForm({ token }: { token: string | undefined }): React.JSX.Element {
  const [password, setPassword] = useState('');
  const reset = useMutation(resetResetPasswordApiV1AuthResetPasswordPostMutation());

  if (reset.isSuccess) {
    return (
      <AuthCard
        title={MESSAGES.auth.resetPassword.doneTitle}
        description={MESSAGES.auth.resetPassword.doneText}
      >
        <Link to="/sign-in" className={buttonVariants()}>
          {MESSAGES.auth.resetPassword.signIn}
        </Link>
      </AuthCard>
    );
  }

  if (token === undefined) {
    return (
      <AuthCard title={MESSAGES.auth.resetPassword.title}>
        <ErrorAlert message={MESSAGES.auth.resetPassword.missingToken} />
        <Link to="/forgot-password" className={buttonVariants({ variant: 'outline' })}>
          {MESSAGES.auth.resetPassword.askAgain}
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={MESSAGES.auth.resetPassword.title}
      description={MESSAGES.auth.resetPassword.description}
    >
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          reset.mutate({ body: { token, password } });
        }}
      >
        <TextField
          label={MESSAGES.auth.fields.newPassword}
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {reset.isError ? <ErrorAlert message={describeError(reset.error)} /> : null}
        <Button type="submit" disabled={reset.isPending}>
          {reset.isPending
            ? MESSAGES.auth.resetPassword.submitting
            : MESSAGES.auth.resetPassword.submit}
        </Button>
      </form>
      {reset.isError ? (
        <Link to="/forgot-password" className={buttonVariants({ variant: 'outline' })}>
          {MESSAGES.auth.resetPassword.askAgain}
        </Link>
      ) : null}
    </AuthCard>
  );
}
