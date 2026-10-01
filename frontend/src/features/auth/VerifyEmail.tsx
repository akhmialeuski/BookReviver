import { useMutation } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useRef } from 'react';
import { verifyVerifyApiV1AuthVerifyPostMutation } from '@/api/@tanstack/react-query.gen';
import { AuthCard } from '@/features/auth/AuthCard';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Alert, AlertDescription } from '@/shared/ui/alert';
import { buttonVariants } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The page the confirmation link of the mail opens. It sends the token of the link to the server once, as soon as
 * it is shown, and tells whether the address is confirmed.
 */

export function VerifyEmail({ token }: { token: string | undefined }): React.JSX.Element {
  const verify = useMutation(verifyVerifyApiV1AuthVerifyPostMutation());
  // A token works once, and React runs an effect twice in development, so the request is guarded
  const started = useRef(false);

  useEffect(() => {
    if (token === undefined || started.current) {
      return;
    }
    started.current = true;
    verify.mutate({ body: { token } });
  }, [token, verify.mutate]);

  let status: React.JSX.Element;
  if (token === undefined) {
    status = <ErrorAlert message={MESSAGES.auth.verify.missingToken} />;
  } else if (verify.isError) {
    status = <ErrorAlert message={describeError(verify.error)} />;
  } else if (verify.isSuccess) {
    status = (
      <Alert>
        <AlertDescription>{MESSAGES.auth.verify.done}</AlertDescription>
      </Alert>
    );
  } else {
    status = <p className="text-sm text-muted-foreground">{MESSAGES.auth.verify.pending}</p>;
  }

  return (
    <AuthCard title={MESSAGES.auth.verify.title}>
      {status}
      <Link to="/sign-in" className={buttonVariants({ variant: 'outline' })}>
        {MESSAGES.auth.verify.signIn}
      </Link>
    </AuthCard>
  );
}
