import { describe, expect, it } from 'vitest';
import { choiceOf, horizontalChoices, VERTICAL_CHOICES } from '@/features/editors/alignment';

/** The alignments the Margins step offers, which follow how the side margins are told apart. */

describe('horizontalChoices', () => {
  it('goes from the gutter to the outer edge when the margins are told apart by the side of the book', () => {
    expect(horizontalChoices('inner-outer')).toEqual(['inner', 'center', 'outer']);
    expect(horizontalChoices(undefined)).toEqual(['inner', 'center', 'outer']);
  });

  it('goes from left to right when the margins are the same on every page', () => {
    expect(horizontalChoices('left-right')).toEqual(['left', 'center', 'right']);
  });
});

describe('choiceOf', () => {
  it('reads a setting as a choice', () => {
    expect(choiceOf('bottom', VERTICAL_CHOICES, 'top')).toBe('bottom');
  });

  it('falls back to where the step starts when the setting is none of the choices', () => {
    expect(choiceOf('inner', horizontalChoices('left-right'), 'center')).toBe('center');
    expect(choiceOf(undefined, VERTICAL_CHOICES, 'top')).toBe('top');
  });
});
