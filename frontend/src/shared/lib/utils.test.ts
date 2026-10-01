import { describe, expect, it } from 'vitest';
import { cn } from './utils';

/**
 * The class name helper: falsy values vanish and a later Tailwind utility replaces a conflicting earlier one.
 */

describe('cn', () => {
  it('joins the class names it is given', () => {
    expect(cn('flex', 'items-center')).toBe('flex items-center');
  });

  it('drops falsy values and flattens arrays and objects', () => {
    expect(cn('p-2', false, undefined, null, ['m-1'], { hidden: true, block: false })).toBe(
      'p-2 m-1 hidden',
    );
  });

  it('lets the later of two conflicting utilities win', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4');
  });

  it('keeps utilities that do not conflict', () => {
    expect(cn('text-sm', 'text-red-500')).toBe('text-sm text-red-500');
  });
});
