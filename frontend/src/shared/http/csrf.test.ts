import { describe, expect, it } from 'vitest';
import { attachCsrfToken, CSRF_HEADER_NAME, isMutating, readCookie } from './csrf';

/**
 * The CSRF helpers: which requests need the header, and where its value comes from.
 */

describe('readCookie', () => {
  it.each([
    ['csrftoken=abc', 'abc'],
    ['theme=dark; csrftoken=abc.def-ghi_jkl; other=1', 'abc.def-ghi_jkl'],
    ['xcsrftoken=wrong; csrftoken=right', 'right'],
  ])('finds the token in %s', (cookies, expected) => {
    expect(readCookie('csrftoken', cookies)).toBe(expected);
  });

  it('returns null when the cookie is not set', () => {
    expect(readCookie('csrftoken', '')).toBeNull();
    expect(readCookie('csrftoken', 'theme=dark')).toBeNull();
  });
});

describe('isMutating', () => {
  it.each(['POST', 'put', 'PATCH', 'DELETE'])('%s changes data', (method) => {
    expect(isMutating(method)).toBe(true);
  });

  it.each(['GET', 'head', 'OPTIONS'])('%s only reads', (method) => {
    expect(isMutating(method)).toBe(false);
  });
});

describe('attachCsrfToken', () => {
  it('copies the cookie into the header of a mutating request', () => {
    const request = new Request('http://localhost/api', { method: 'POST' });

    expect(attachCsrfToken(request, 'csrftoken=abc')).toBe(true);
    expect(request.headers.get(CSRF_HEADER_NAME)).toBe('abc');
  });

  it('leaves a reading request without the header', () => {
    const request = new Request('http://localhost/api');

    expect(attachCsrfToken(request, 'csrftoken=abc')).toBe(true);
    expect(request.headers.has(CSRF_HEADER_NAME)).toBe(false);
  });

  it('reports a mutating request that has no token yet', () => {
    const request = new Request('http://localhost/api', { method: 'DELETE' });

    expect(attachCsrfToken(request, '')).toBe(false);
    expect(request.headers.has(CSRF_HEADER_NAME)).toBe(false);
  });
});
