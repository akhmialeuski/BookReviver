/**
 * The double-submit CSRF protection of the API, seen from the browser.
 *
 * The server issues the `csrftoken` cookie on any GET, and a mutating request repeats its value in the
 * `x-csrftoken` header. A cross-site page can make the browser send the cookie but cannot read it, so it cannot
 * set the header. These helpers read the cookie and decide which requests carry the header.
 */

export const CSRF_COOKIE_NAME = 'csrftoken';
export const CSRF_HEADER_NAME = 'x-csrftoken';

// Methods that only read, which the server does not check and which therefore carry no header
const SAFE_METHODS: ReadonlySet<string> = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);

/** Return the value of a cookie from the text of `document.cookie`, or null when it is not set. */
export function readCookie(name: string, cookies: string): string | null {
  const prefix = `${name}=`;
  for (const part of cookies.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return trimmed.slice(prefix.length);
    }
  }
  return null;
}

/** Tell whether a request method changes data and so needs the CSRF header. */
export function isMutating(method: string): boolean {
  return !SAFE_METHODS.has(method.toUpperCase());
}

/**
 * Set the CSRF header of a request from the cookie text when the method mutates and the cookie exists.
 *
 * @returns Whether the request now carries the header, or does not need it. False means a mutating request has no
 * token yet and the caller has to fetch one.
 */
export function attachCsrfToken(request: Request, cookies: string): boolean {
  if (!isMutating(request.method)) {
    return true;
  }
  const token = readCookie(CSRF_COOKIE_NAME, cookies);
  if (token === null) {
    return false;
  }
  request.headers.set(CSRF_HEADER_NAME, token);
  return true;
}
