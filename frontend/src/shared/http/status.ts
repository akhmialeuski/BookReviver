/**
 * The HTTP status codes the interface tells apart, named once so no screen compares against a bare number.
 */

export const HttpStatus = {
  NoContent: 204,
  BadRequest: 400,
  Unauthorized: 401,
  Forbidden: 403,
  NotFound: 404,
  RequestTimeout: 408,
  Conflict: 409,
  PayloadTooLarge: 413,
  TooManyRequests: 429,
  UnprocessableContent: 422,
  InternalServerError: 500,
} as const;

/** One of the status codes of {@link HttpStatus}. */
export type HttpStatus = (typeof HttpStatus)[keyof typeof HttpStatus];
