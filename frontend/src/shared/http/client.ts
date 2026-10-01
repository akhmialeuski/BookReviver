import { usersCurrentUserApiV1UsersMeGet } from '@/api';
import { client } from '@/api/client.gen';
import { attachCsrfToken } from '@/shared/http/csrf';
import { parseProblem } from '@/shared/http/problem';

/**
 * Configures the generated API client for the browser: cookies, the CSRF header and error messages.
 *
 * The generated client is never edited, so its behaviour is changed through its interceptors only. The request
 * interceptor adds `x-csrftoken` to every mutating request, and the error interceptor turns every failure into a
 * `ProblemError`, so `throwOnError` callers and the TanStack Query hooks all see the same error type.
 */

/**
 * Add the CSRF header to a request, first asking the server for a token when the browser has none yet.
 *
 * Any GET makes the server set the cookie. The first sign-in or registration is the usual case that has none.
 */
export async function csrfInterceptor(request: Request, cookies: () => string): Promise<Request> {
  if (!attachCsrfToken(request, cookies())) {
    await usersCurrentUserApiV1UsersMeGet({ throwOnError: false });
    attachCsrfToken(request, cookies());
  }
  return request;
}

/**
 * Install the interceptors on the generated client, once, when the application starts.
 *
 * @param cookies Returns the cookies of the page as `document.cookie` does; a parameter so tests can use a fake jar.
 */
export function configureApiClient(cookies: () => string): void {
  client.setConfig({ credentials: 'same-origin' });
  client.interceptors.request.use((request) => csrfInterceptor(request, cookies));
  client.interceptors.error.use((error, response) => parseProblem(error, response?.status ?? null));
}
