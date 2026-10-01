import { createFileRoute } from '@tanstack/react-router';
import { VerifyEmail } from '@/features/auth/VerifyEmail';

/**
 * The address the confirmation link of the registration mail opens, `/verify-email?token=...`.
 */

export const Route = createFileRoute('/verify-email')({
  validateSearch: (search: Record<string, unknown>): { token?: string } =>
    typeof search.token === 'string' && search.token !== '' ? { token: search.token } : {},
  component: VerifyEmailPage,
});

function VerifyEmailPage(): React.JSX.Element {
  const { token } = Route.useSearch();
  return <VerifyEmail token={token} />;
}
