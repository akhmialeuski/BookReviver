import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { ProviderButtons } from './ProviderButtons';

/**
 * Which buttons the sign-in screens show for the providers the server lists.
 */

const GOOGLE = { name: 'google', label: 'Google' };
const FACEBOOK = { name: 'facebook', label: 'Facebook' };

function render(providers: { name: string; label: string }[], disabled = false): string {
  return renderToStaticMarkup(
    <ProviderButtons providers={providers} disabled={disabled} onChoose={vi.fn()} />,
  );
}

describe('ProviderButtons', () => {
  it('shows nothing when the server offers no provider', () => {
    expect(render([])).toBe('');
  });

  it('shows a button for each listed provider and for no other', () => {
    const markup = render([GOOGLE]);

    expect(markup).toContain('Continue with Google');
    expect(markup).not.toContain('Facebook');
  });

  it('keeps the order of the list', () => {
    const markup = render([FACEBOOK, GOOGLE]);

    expect(markup.indexOf('Continue with Facebook')).toBeLessThan(
      markup.indexOf('Continue with Google'),
    );
  });

  it('disables the buttons while a sign-in is starting', () => {
    // The class names carry `disabled:` variants, so the attribute is what tells
    expect(render([GOOGLE], true)).toContain('disabled=""');
    expect(render([GOOGLE], false)).not.toContain('disabled=""');
  });
});
