import { HttpStatus } from '@/shared/http/status';
import { MESSAGES } from '@/shared/messages';

/**
 * Reading the errors of the API as messages a person can act on.
 *
 * Every error is an RFC 9457 problem document (`application/problem+json`), but its `detail` is a string for most
 * routes, an object with a `code` and a `reason` for the password rules, and absent for a validation error, which
 * carries an `errors` list instead. A response that is no document at all, or no response, is told apart too.
 * `ProblemError` is what the client throws, so every screen shows `error.message`.
 */

// A detail written in capitals and underscores is a code of the account routes, not a sentence
const CODE_PATTERN = /^[A-Z][A-Z0-9_]+$/;

export interface FieldError {
  /** Name of the request field the error is about, the last element of its location. */
  field: string;
  message: string;
}

/** An error response of the API, with the message to show and the parts it was made from. */
export class ProblemError extends Error {
  /** HTTP status, or null when the server did not answer. */
  readonly status: number | null;
  /** Code of the account routes such as `LOGIN_BAD_CREDENTIALS`, or null. */
  readonly code: string | null;
  readonly fieldErrors: readonly FieldError[];

  constructor(
    message: string,
    status: number | null,
    code: string | null,
    fieldErrors: readonly FieldError[],
  ) {
    super(message);
    this.name = 'ProblemError';
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readFieldErrors(errors: unknown): FieldError[] {
  if (!Array.isArray(errors)) {
    return [];
  }
  return errors.flatMap((entry: unknown): FieldError[] => {
    if (!isRecord(entry) || typeof entry.msg !== 'string') {
      return [];
    }
    const location = Array.isArray(entry.loc) ? entry.loc : [];
    const field = String(location.at(-1) ?? '');
    return [{ field, message: entry.msg }];
  });
}

function messageForStatus(status: number | null): string {
  if (status === null) {
    return MESSAGES.problems.network;
  }
  switch (status) {
    case HttpStatus.Unauthorized:
      return MESSAGES.problems.unauthorized;
    case HttpStatus.Forbidden:
      return MESSAGES.problems.forbidden;
    case HttpStatus.NotFound:
      return MESSAGES.problems.notFound;
    case HttpStatus.Conflict:
      return MESSAGES.problems.conflict;
    case HttpStatus.PayloadTooLarge:
      return MESSAGES.problems.tooLarge;
    default:
      return MESSAGES.problems.unknown;
  }
}

/**
 * Turn the body of an error response, or the failure of a request that got no response, into a `ProblemError`.
 *
 * @param body The parsed JSON of the response, its text, or the error a failed `fetch` threw.
 * @param status The HTTP status, or null when no response arrived.
 */
export function parseProblem(body: unknown, status: number | null): ProblemError {
  if (body instanceof ProblemError) {
    return body;
  }
  if (!isRecord(body)) {
    return new ProblemError(messageForStatus(status), status, null, []);
  }
  const resolvedStatus = typeof body.status === 'number' ? body.status : status;
  const detail = body.detail;

  let code: string | null = null;
  let sentence: string | null = null;
  if (typeof detail === 'string') {
    if (CODE_PATTERN.test(detail)) {
      code = detail;
    } else if (detail.trim() !== '') {
      sentence = detail;
    }
  } else if (isRecord(detail)) {
    code = typeof detail.code === 'string' ? detail.code : null;
    sentence = typeof detail.reason === 'string' ? detail.reason : null;
  }

  const fieldErrors = readFieldErrors(body.errors);
  const known = code === null ? undefined : MESSAGES.problems.codes[code];
  let message: string;
  if (known !== undefined) {
    message = known;
  } else if (sentence !== null && (resolvedStatus ?? 0) < HttpStatus.InternalServerError) {
    message = sentence;
  } else if (fieldErrors.length > 0 || resolvedStatus === HttpStatus.UnprocessableContent) {
    const lines = fieldErrors.map((error) => `${error.field}: ${error.message}`);
    message = [MESSAGES.problems.invalidInput, ...lines].join(' ');
  } else {
    message = messageForStatus(resolvedStatus);
  }
  return new ProblemError(message, resolvedStatus, code, fieldErrors);
}

/** Return the message to show for anything a request or a mutation can throw. */
export function describeError(error: unknown): string {
  return error instanceof ProblemError ? error.message : MESSAGES.problems.unknown;
}
