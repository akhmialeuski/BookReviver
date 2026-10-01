import { describeError, ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';
import { MESSAGES } from '@/shared/messages';

/**
 * The words for a failed change of pages.
 *
 * A change of pages fails with a 409 when another change got to the same place first, when a page is moved next to
 * itself, or when a scan is already shown by another page. The server's sentence says which, and the screen adds that
 * the list has been read again, so the reader knows what they see is current and can try once more.
 */

/**
 * Write an error of a page change as a message.
 *
 * @param error What the request threw.
 * @returns The conflict wording with the server's reason for a 409, and the usual message for anything else.
 */
export function describePageError(error: unknown): string {
  if (error instanceof ProblemError && error.status === HttpStatus.Conflict) {
    const reason = error.message === MESSAGES.problems.conflict ? '' : error.message;
    return MESSAGES.pages.conflict(reason);
  }
  return describeError(error);
}
