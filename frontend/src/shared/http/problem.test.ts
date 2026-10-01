import { describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/messages';
import { describeError, ProblemError, parseProblem } from './problem';

/**
 * Parsing of problem documents, with the response bodies the backend really sends.
 */

describe('parseProblem', () => {
  it('maps a code of the account routes to its sentence', () => {
    const error = parseProblem(
      {
        type: 'http-bad-request',
        title: 'Bad Request',
        status: 400,
        detail: 'LOGIN_BAD_CREDENTIALS',
      },
      400,
    );

    expect(error.message).toBe(MESSAGES.problems.codes.LOGIN_BAD_CREDENTIALS);
    expect(error.code).toBe('LOGIN_BAD_CREDENTIALS');
    expect(error.status).toBe(400);
  });

  it('shows the reason of a coded object detail, as the password rules send it', () => {
    const error = parseProblem(
      {
        type: 'http-bad-request',
        title: 'Bad Request',
        status: 400,
        detail: {
          code: 'REGISTER_INVALID_PASSWORD',
          reason: 'Use a password of at least 12 characters.',
        },
      },
      400,
    );

    expect(error.message).toBe('Use a password of at least 12 characters.');
    expect(error.code).toBe('REGISTER_INVALID_PASSWORD');
  });

  it('shows a sentence detail as it is', () => {
    const error = parseProblem(
      {
        type: 'forbidden',
        title: 'Forbidden',
        status: 403,
        detail: 'The request carries no valid CSRF token. Reload the page and try again.',
      },
      403,
    );

    expect(error.message).toBe(
      'The request carries no valid CSRF token. Reload the page and try again.',
    );
  });

  it('lists the fields of a validation problem, which has no detail', () => {
    const error = parseProblem(
      {
        type: 'request-validation-failed',
        title: 'Request validation error.',
        status: 422,
        errors: [{ type: 'value_error', loc: ['body', 'email'], msg: 'not a valid email address' }],
      },
      422,
    );

    expect(error.message).toBe(
      `${MESSAGES.problems.invalidInput} email: not a valid email address`,
    );
    expect(error.fieldErrors).toEqual([{ field: 'email', message: 'not a valid email address' }]);
  });

  it.each([
    [401, MESSAGES.problems.unauthorized],
    [403, MESSAGES.problems.forbidden],
    [404, MESSAGES.problems.notFound],
    [409, MESSAGES.problems.conflict],
    [413, MESSAGES.problems.tooLarge],
    [500, MESSAGES.problems.unknown],
  ])('words status %d without a detail', (status, expected) => {
    const error = parseProblem({ type: 'x', title: 'x', status }, status);

    expect(error.message).toBe(expected);
  });

  it('hides the detail of a server error from the user', () => {
    const error = parseProblem(
      { type: 'x', title: 'x', status: 500, detail: 'Traceback in module a.b' },
      500,
    );

    expect(error.message).toBe(MESSAGES.problems.unknown);
  });

  it('reports a failed request with no response as a network problem', () => {
    const error = parseProblem(new TypeError('Failed to fetch'), null);

    expect(error.message).toBe(MESSAGES.problems.network);
    expect(error.status).toBeNull();
  });

  it('words a response that is not a document by its status', () => {
    expect(parseProblem('<html>Bad gateway</html>', 502).message).toBe(MESSAGES.problems.unknown);
  });

  it('returns a ProblemError it was given unchanged', () => {
    const original = new ProblemError('kept', 409, null, []);

    expect(parseProblem(original, 500)).toBe(original);
  });
});

describe('describeError', () => {
  it('shows the message of a problem and a generic one for any other error', () => {
    expect(describeError(new ProblemError('Shown.', 400, null, []))).toBe('Shown.');
    expect(describeError(new Error('internal'))).toBe(MESSAGES.problems.unknown);
  });
});
