import { describe, expect, it } from 'vitest';
import { ProblemCode } from '@/shared/http/codes';
import { failureOf, OAuthFailure, parseFailure, readCallback } from './oauth';

/**
 * What the callback page makes of the provider's answer: the parameters of its address, the failures the backend's
 * answer maps to, and the failure names the sign-in address may carry.
 */

describe('readCallback', () => {
  it('reads the code and the state a provider returns', () => {
    expect(readCallback({ code: 'abc', state: 'signed' })).toEqual({
      kind: 'authorized',
      code: 'abc',
      state: 'signed',
    });
  });

  it.each([
    ['the user declined', { error: 'access_denied', state: 'signed' }],
    ['an error comes with a code', { error: 'server_error', code: 'abc', state: 'signed' }],
    ['there is no code', { state: 'signed' }],
    ['there is no state', { code: 'abc' }],
    ['the code is empty', { code: '', state: 'signed' }],
    ['the code is not text', { code: 7, state: 'signed' }],
    ['nothing was sent', {}],
  ])('counts it as refused when %s', (_case, search) => {
    expect(readCallback(search)).toEqual({ kind: 'refused' });
  });
});

describe('failureOf', () => {
  it.each([
    [ProblemCode.OAuthInvalidState, OAuthFailure.InvalidState],
    [ProblemCode.AccessTokenDecodeError, OAuthFailure.InvalidState],
    [ProblemCode.AccessTokenExpired, OAuthFailure.InvalidState],
    [ProblemCode.OAuthNoEmail, OAuthFailure.NoEmail],
    ['OAUTH_USER_ALREADY_EXISTS', OAuthFailure.Failed],
    [null, OAuthFailure.Failed],
  ])('maps the code %s to %s', (code, failure) => {
    expect(failureOf(code)).toBe(failure);
  });
});

describe('parseFailure', () => {
  it('accepts the names of the failures and nothing else', () => {
    expect(Object.values(OAuthFailure).map(parseFailure)).toEqual(Object.values(OAuthFailure));
    expect(parseFailure('<script>')).toBeUndefined();
    expect(parseFailure(undefined)).toBeUndefined();
  });
});
