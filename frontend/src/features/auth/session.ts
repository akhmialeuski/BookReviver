import { type QueryClient, type UseQueryResult, useQuery } from '@tanstack/react-query';
import type { AccountRead, UsersCurrentUserApiV1UsersMeGetError } from '@/api';
import { usersCurrentUserApiV1UsersMeGetOptions } from '@/api/@tanstack/react-query.gen';
import { ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';

/**
 * The signed-in account, read from `GET /users/me`.
 *
 * The session is an opaque cookie the browser keeps, so whether someone is signed in is known only by asking the
 * server. The answer is a normal query: route guards read it through `fetchSession`, components through
 * `useSession`, and signing out or a rejected request clears it.
 */

/** Options of the session query; a 401 is an answer, not a failure worth retrying. */
export function sessionQuery(): ReturnType<typeof usersCurrentUserApiV1UsersMeGetOptions> {
  return { ...usersCurrentUserApiV1UsersMeGetOptions(), retry: false };
}

/** Tell whether an error is the server saying the session is missing or has ended. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ProblemError && error.status === HttpStatus.Unauthorized;
}

/** Return the signed-in account, or null for a visitor, asking the server when the answer is not cached. */
export async function fetchSession(queryClient: QueryClient): Promise<AccountRead | null> {
  try {
    return await queryClient.ensureQueryData(sessionQuery());
  } catch (error) {
    if (isUnauthorized(error)) {
      return null;
    }
    throw error;
  }
}

/** The signed-in account for a component, with the state of the request. */
export function useSession(): UseQueryResult<AccountRead, UsersCurrentUserApiV1UsersMeGetError> {
  return useQuery(sessionQuery());
}
