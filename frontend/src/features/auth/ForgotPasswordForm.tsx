import { useMutation } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { resetForgotPasswordApiV1AuthForgotPasswordPostMutation } from '@/api/@tanstack/react-query.gen';
import { AuthCard } from '@/features/auth/AuthCard';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button, buttonVariants } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextField } from '@/shared/ui/text-field';

/**
 * The form that asks for a password reset mail. The server answers the same whether or not the address has an
 * account, so the page does too, and never says which addresses are registered.
 */

export function ForgotPasswordForm(): React.JSX.Element {
  const [email, setEmail] = useState('');
  const forgot = useMutation(resetForgotPasswordApiV1AuthForgotPasswordPostMutation());

  if (forgot.isSuccess) {
    return (
      <AuthCard
        title={MESSAGES.auth.forgotPassword.doneTitle}
        description={MESSAGES.auth.forgotPassword.doneText}
      >
        <Link to="/sign-in" className={buttonVariants({ variant: 'outline' })}>
          {MESSAGES.auth.forgotPassword.back}
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={MESSAGES.auth.forgotPassword.title}
      description={MESSAGES.auth.forgotPassword.description}
    >
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          forgot.mutate({ body: { email } });
        }}
      >
        <TextField
          label={MESSAGES.auth.fields.email}
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        {forgot.isError ? <ErrorAlert message={describeError(forgot.error)} /> : null}
        <Button type="submit" disabled={forgot.isPending}>
          {forgot.isPending
            ? MESSAGES.auth.forgotPassword.submitting
            : MESSAGES.auth.forgotPassword.submit}
        </Button>
      </form>
      <p className="text-center text-sm text-muted-foreground">
        <Link to="/sign-in" className="text-foreground underline underline-offset-4">
          {MESSAGES.auth.forgotPassword.back}
        </Link>
      </p>
    </AuthCard>
  );
}
