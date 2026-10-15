import { MutationObserver } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';
import { MESSAGES } from '@/shared/messages';
import { queryClient } from './query-client';

/**
 * The retry policy of the application's query client for mutations.
 *
 * A mutation changes the server, and a book that is busy answers 503 for the very reason that another change is in
 * flight, so sending the same change again would only repeat it. The client retries queries, never mutations.
 */

describe('the application query client', () => {
  it('sends a mutation once when the server answers that it is busy', async () => {
    const mutationFn = vi
      .fn<() => Promise<void>>()
      .mockRejectedValue(
        new ProblemError(MESSAGES.problems.busy, HttpStatus.ServiceUnavailable, null, []),
      );
    const observer = new MutationObserver(queryClient, { mutationFn });

    await expect(observer.mutate()).rejects.toMatchObject({
      status: HttpStatus.ServiceUnavailable,
      message: MESSAGES.problems.busy,
    });
    expect(mutationFn).toHaveBeenCalledTimes(1);
  });
});
