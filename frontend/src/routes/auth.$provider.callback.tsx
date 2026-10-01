import { createFileRoute } from '@tanstack/react-router';
import { OAuthCallback } from '@/features/auth/OAuthCallback';
import { type CallbackParams, readCallback } from '@/features/auth/oauth';

/**
 * The address a social provider returns the browser to, `/auth/{provider}/callback?code=...&state=...`, which is
 * the redirect URI to enter in the provider's console.
 */

export const Route = createFileRoute('/auth/$provider/callback')({
  validateSearch: (search: Record<string, unknown>): CallbackParams => readCallback(search),
  component: CallbackPage,
});

function CallbackPage(): React.JSX.Element {
  const { provider } = Route.useParams();
  const params = Route.useSearch();
  return <OAuthCallback provider={provider} params={params} />;
}
