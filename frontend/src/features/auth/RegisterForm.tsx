import { useMutation } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { registerRegisterApiV1AuthRegisterPostMutation } from '@/api/@tanstack/react-query.gen';
import { AuthCard } from '@/features/auth/AuthCard';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button, buttonVariants } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextField } from '@/shared/ui/text-field';

/**
 * The registration form. The server answers the same way for a new address and for one that already has an account,
 * so the page that follows never says which it was; it only asks the visitor to open the mailed link.
 */

export function RegisterForm(): React.JSX.Element {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const register = useMutation(registerRegisterApiV1AuthRegisterPostMutation());

  if (register.isSuccess) {
    return (
      <AuthCard
        title={MESSAGES.auth.register.doneTitle}
        description={MESSAGES.auth.register.doneText}
      >
        <Link to="/sign-in" className={buttonVariants({ variant: 'outline' })}>
          {MESSAGES.auth.register.signIn}
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={MESSAGES.auth.register.title} description={MESSAGES.auth.register.description}>
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          register.mutate({ body: { email, password } });
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
        <TextField
          label={MESSAGES.auth.fields.password}
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          hint={MESSAGES.auth.register.passwordHint}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {register.isError ? <ErrorAlert message={describeError(register.error)} /> : null}
        <Button type="submit" disabled={register.isPending}>
          {register.isPending ? MESSAGES.auth.register.submitting : MESSAGES.auth.register.submit}
        </Button>
      </form>
      <p className="text-center text-sm text-muted-foreground">
        {MESSAGES.auth.register.hasAccount}{' '}
        <Link to="/sign-in" className="text-foreground underline underline-offset-4">
          {MESSAGES.auth.register.signIn}
        </Link>
      </p>
    </AuthCard>
  );
}
