/**
 * The codes the account routes of the backend put in the `detail` of a problem document.
 *
 * They come from FastAPI Users and are part of its contract, so they are named once here, and both the message
 * table and the screens that react to one of them refer to this object.
 */

export const ProblemCode = {
  LoginBadCredentials: 'LOGIN_BAD_CREDENTIALS',
  LoginUserNotVerified: 'LOGIN_USER_NOT_VERIFIED',
  VerifyBadToken: 'VERIFY_USER_BAD_TOKEN',
  VerifyAlreadyVerified: 'VERIFY_USER_ALREADY_VERIFIED',
} as const;

/** One code of {@link ProblemCode}. */
export type ProblemCode = (typeof ProblemCode)[keyof typeof ProblemCode];
