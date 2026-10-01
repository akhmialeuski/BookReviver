import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';
import { sessionQuery } from './session';
import { watchForUnauthorized } from './unauthorized';

/**
 * The watcher that sends a visitor back to sign-in: which failures it reacts to, and the one it must not.
 */

function problem(status: number): ProblemError {
  return new ProblemError('failed', status, null, []);
}

describe('watchForUnauthorized', () => {
  let queryClient: QueryClient;
  let onUnauthorized: ReturnType<typeof vi.fn<() => void>>;
  let stop: () => void;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    onUnauthorized = vi.fn<() => void>();
    stop = watchForUnauthorized(queryClient, onUnauthorized);
  });

  afterEach(() => {
    stop();
  });

  it('reacts to a 401 of an ordinary query', async () => {
    await queryClient
      .fetchQuery({
        queryKey: ['books'],
        queryFn: () => Promise.reject(problem(HttpStatus.Unauthorized)),
      })
      .catch(() => undefined);

    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('reacts to a 401 of a mutation', async () => {
    await queryClient
      .getMutationCache()
      .build(queryClient, { mutationFn: () => Promise.reject(problem(HttpStatus.Unauthorized)) })
      .execute(undefined)
      .catch(() => undefined);

    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('does not react to the 401 of the session query, which is how the guard learns nobody is signed in', async () => {
    await queryClient
      .fetchQuery({
        queryKey: sessionQuery().queryKey,
        queryFn: () => Promise.reject(problem(HttpStatus.Unauthorized)),
      })
      .catch(() => undefined);

    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it.each([HttpStatus.Forbidden, HttpStatus.NotFound, HttpStatus.InternalServerError])(
    'does not react to a %d',
    async (status) => {
      await queryClient
        .fetchQuery({ queryKey: ['books'], queryFn: () => Promise.reject(problem(status)) })
        .catch(() => undefined);

      expect(onUnauthorized).not.toHaveBeenCalled();
    },
  );

  it('stops reacting once stopped', async () => {
    stop();

    await queryClient
      .fetchQuery({
        queryKey: ['books'],
        queryFn: () => Promise.reject(problem(HttpStatus.Unauthorized)),
      })
      .catch(() => undefined);

    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
