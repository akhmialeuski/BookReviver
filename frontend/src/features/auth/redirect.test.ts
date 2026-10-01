import { describe, expect, it } from 'vitest';
import { DEFAULT_LANDING, safeRedirect } from './redirect';

/**
 * The rule that keeps the sign-in redirect inside the application.
 */

describe('safeRedirect', () => {
  it.each(['/projects', '/projects/5?page=2', '/'])('keeps the local path %s', (path) => {
    expect(safeRedirect(path)).toBe(path);
  });

  it.each([
    'https://evil.example/projects',
    '//evil.example',
    '/\\evil.example',
    'projects',
    '',
    undefined,
    42,
    null,
  ])('falls back to the landing path for %s', (value) => {
    expect(safeRedirect(value)).toBe(DEFAULT_LANDING);
  });
});
