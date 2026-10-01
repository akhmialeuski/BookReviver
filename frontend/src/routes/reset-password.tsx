import { createFileRoute } from '@tanstack/react-router';
import { ResetPasswordForm } from '@/features/auth/ResetPasswordForm';

/**
 * The address the reset link of the mail opens, `/reset-password?token=...`.
 */

export const Route = createFileRoute('/reset-password')({
  validateSearch: (search: Record<string, unknown>): { token?: string } =>
    typeof search.token === 'string' && search.token !== '' ? { token: search.token } : {},
  component: ResetPasswordPage,
});

function ResetPasswordPage(): React.JSX.Element {
  const { token } = Route.useSearch();
  return <ResetPasswordForm token={token} />;
}
