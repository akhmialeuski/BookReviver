import { ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';

/**
 * Which failed requests are worth sending again.
 *
 * A retry helps only when the failure may pass by itself: no connection, a server error, a timeout, a rate limit.
 * Every other client error says something about the request itself, such as an ended session, a missing book or
 * invalid input, and the same request fails the same way, so retrying only delays the answer the screen needs.
 */

const MAX_RETRIES = 3;

/** Tell whether a request that failed `failureCount` times with `error` should be sent again. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) {
    return false;
  }
  if (!(error instanceof ProblemError) || error.status === null) {
    return true;
  }
  const isClientError =
    error.status >= HttpStatus.BadRequest && error.status < HttpStatus.InternalServerError;
  const mayPass =
    error.status === HttpStatus.RequestTimeout || error.status === HttpStatus.TooManyRequests;
  return !isClientError || mayPass;
}
