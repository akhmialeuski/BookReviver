import { ProblemCode } from '@/shared/http/codes';

/**
 * Signing in with a social provider, from the parts that need no browser.
 *
 * The provider returns the browser to the page `/auth/{name}/callback` with `code` and `state` in the address, or
 * with `error` when the user declined. `readCallback` tells which of those arrived, and `failureOf` turns what the
 * backend answered into one of the few failures the sign-in screen has a message for. Both are plain functions so
 * the cases are tested without a browser.
 */

/** Why a sign-in with a provider did not happen; the sign-in screen has one message for each. */
export const OAuthFailure = {
  /** The user declined at the provider, or the provider sent the browser back without a code. */
  Refused: 'refused',
  /** The state of the request does not match this browser's, which a forged or an old link causes. */
  InvalidState: 'invalid-state',
  /** The provider did not tell the address of the account, so there is nothing to sign in. */
  NoEmail: 'no-email',
  /** Anything else, such as a provider that was unreachable. */
  Failed: 'failed',
} as const;

/** One reason of {@link OAuthFailure}. */
export type OAuthFailure = (typeof OAuthFailure)[keyof typeof OAuthFailure];

/** The parameters of the callback address, read once. */
export type CallbackParams =
  | { readonly kind: 'authorized'; readonly code: string; readonly state: string }
  | { readonly kind: 'refused' };

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

/**
 * Read what the provider put into the address of the callback page.
 *
 * An `error` parameter wins over everything else, since a provider that reports an error sends no usable code, and a
 * code without a state cannot be checked, so both count as a refusal.
 *
 * @param search The query parameters of the page, as the router parsed them.
 */
export function readCallback(search: Record<string, unknown>): CallbackParams {
  const code = text(search.code);
  const state = text(search.state);
  if (text(search.error) !== undefined || code === undefined || state === undefined) {
    return { kind: 'refused' };
  }
  return { kind: 'authorized', code, state };
}

const STATE_CODES: ReadonlySet<string> = new Set([
  ProblemCode.OAuthInvalidState,
  ProblemCode.AccessTokenDecodeError,
  ProblemCode.AccessTokenExpired,
]);

/**
 * Return the failure the sign-in screen shows for what the backend's callback answered.
 *
 * @param code The code of the problem the callback answered with, or null when it had none.
 */
export function failureOf(code: string | null): OAuthFailure {
  if (code === null) {
    return OAuthFailure.Failed;
  }
  if (STATE_CODES.has(code)) {
    return OAuthFailure.InvalidState;
  }
  if (code === ProblemCode.OAuthNoEmail) {
    return OAuthFailure.NoEmail;
  }
  return OAuthFailure.Failed;
}

/** Return `value` when it names a failure of {@link OAuthFailure}, so an address cannot make the screen show text. */
export function parseFailure(value: unknown): OAuthFailure | undefined {
  return Object.values(OAuthFailure).find((failure) => failure === value);
}
