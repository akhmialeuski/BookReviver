import { describe, expect, it } from 'vitest';
import { ProblemError } from './problem';
import { shouldRetry } from './retry';

/**
 * The retry policy: transient failures are retried a few times, failures of the request itself never.
 */

function problem(status: number | null): ProblemError {
  return new ProblemError('failed', status, null, []);
}

describe('shouldRetry', () => {
  it.each([400, 401, 403, 404, 409, 413, 422])(
    'does not retry a %d, which a second try cannot change',
    (status) => {
      expect(shouldRetry(0, problem(status))).toBe(false);
    },
  );

  it.each([408, 429, 500, 502, 503])('retries a %d, which may pass', (status) => {
    expect(shouldRetry(0, problem(status))).toBe(true);
  });

  it('retries when no connection could be made and for an error that is not a problem', () => {
    expect(shouldRetry(0, problem(null))).toBe(true);
    expect(shouldRetry(0, new Error('boom'))).toBe(true);
  });

  it('gives up after three retries', () => {
    expect(shouldRetry(2, problem(503))).toBe(true);
    expect(shouldRetry(3, problem(503))).toBe(false);
  });
});
