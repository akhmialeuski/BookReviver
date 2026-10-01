/**
 * Where to go after signing in.
 *
 * The address to return to travels in the `redirect` search parameter of the sign-in page, which anyone can write
 * into a link. Following it blindly would let a crafted link send a freshly signed-in user to another site, so only
 * a path of this application is accepted.
 */

export const DEFAULT_LANDING = '/projects';

/** Return `value` when it is a path inside this application, and the default landing path otherwise. */
export function safeRedirect(value: unknown): string {
  if (typeof value !== 'string') {
    return DEFAULT_LANDING;
  }
  // `//host` and `/\host` are read by browsers as another origin, so a second slash or a backslash is refused
  const isLocalPath = value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/\\');
  return isLocalPath ? value : DEFAULT_LANDING;
}
