import { useQuery } from '@tanstack/react-query';
import type { SignInProvider } from '@/api';
import { listProvidersApiV1AuthProvidersGetOptions } from '@/api/@tanstack/react-query.gen';
import { client } from '@/api/client.gen';

/**
 * The social providers the server offers, and the two requests of the sign-in with one.
 *
 * The routes of a provider, `/auth/{name}/authorize` and `/auth/{name}/callback`, exist only for a provider whose
 * keys are configured, so they are not in the OpenAPI schema and have no generated function. They go through the
 * generated client all the same, so the CSRF header, the cookies and the error messages behave as everywhere else.
 */

const AUTHORIZE_URL = '/api/v1/auth/{name}/authorize';
const CALLBACK_URL = '/api/v1/auth/{name}/callback';

/** Ask the server which providers it offers; a failed request leaves the list empty and the buttons out. */
export function useProviders(): readonly SignInProvider[] {
  const { data } = useQuery({ ...listProvidersApiV1AuthProvidersGetOptions(), retry: false });
  return data ?? [];
}

/** Ask the server for the address of the provider's consent page, and start the state cookie of the sign-in. */
export async function requestAuthorizationUrl(name: string): Promise<string> {
  const { data } = await client.get<{ 200: { authorization_url: string } }, unknown, true>({
    url: AUTHORIZE_URL,
    path: { name },
    throwOnError: true,
  });
  return data.authorization_url;
}

/** Hand the code and the state of the provider's answer to the server, which signs the browser in. */
export async function completeSignIn(name: string, code: string, state: string): Promise<void> {
  await client.get({
    url: CALLBACK_URL,
    path: { name },
    query: { code, state },
    throwOnError: true,
  });
}
