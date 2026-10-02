import { describe, expect, it } from 'vitest';
import { type BookKeyPress, bookKeyAction, stageOfDigit } from '@/features/workspace/keys';

function press(overrides: Partial<BookKeyPress>): BookKeyPress {
  return {
    key: '',
    code: '',
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    target: null,
    ...overrides,
  };
}

describe('stageOfDigit', () => {
  it('maps 1 to 9 to the first nine stages and 0 to the last', () => {
    expect(stageOfDigit(1)).toBe('import');
    expect(stageOfDigit(4)).toBe('geometry');
    expect(stageOfDigit(9)).toBe('proofreading');
    expect(stageOfDigit(0)).toBe('typesetting');
  });
});

describe('bookKeyAction', () => {
  it('goes to a stage on Alt with its digit, whatever character the layout types', () => {
    expect(bookKeyAction(press({ altKey: true, code: 'Digit4', key: '$' }), false)).toEqual({
      kind: 'stage',
      stage: 'geometry',
    });
    expect(bookKeyAction(press({ altKey: true, code: 'Digit0', key: '0' }), false)).toEqual({
      kind: 'stage',
      stage: 'typesetting',
    });
  });

  it('opens the book description on Alt and I', () => {
    expect(bookKeyAction(press({ altKey: true, code: 'KeyI', key: 'i' }), false)).toEqual({
      kind: 'about',
    });
  });

  it('opens the shortcuts on the question mark, which needs Shift on most layouts', () => {
    expect(bookKeyAction(press({ key: '?', code: 'Slash', shiftKey: true }), false)).toEqual({
      kind: 'shortcuts',
    });
  });

  it('ignores a digit without Alt, and with Ctrl, Meta or Shift as well', () => {
    expect(bookKeyAction(press({ code: 'Digit4', key: '4' }), false)).toBeNull();
    expect(bookKeyAction(press({ altKey: true, ctrlKey: true, code: 'Digit4' }), false)).toBeNull();
    expect(bookKeyAction(press({ altKey: true, metaKey: true, code: 'Digit4' }), false)).toBeNull();
    expect(
      bookKeyAction(press({ altKey: true, shiftKey: true, code: 'Digit4' }), false),
    ).toBeNull();
  });

  it('leaves every key to an open dialog', () => {
    expect(bookKeyAction(press({ altKey: true, code: 'Digit4' }), true)).toBeNull();
    expect(bookKeyAction(press({ key: '?' }), true)).toBeNull();
  });

  it('leaves typing in a field alone', () => {
    const input = document.createElement('input');
    expect(bookKeyAction(press({ key: '?', target: input }), false)).toBeNull();
    expect(bookKeyAction(press({ altKey: true, code: 'Digit4', target: input }), false)).toBeNull();
  });

  it('ignores the keys that are not the screen’s', () => {
    expect(bookKeyAction(press({ key: 'a', code: 'KeyA' }), false)).toBeNull();
    expect(
      bookKeyAction(press({ altKey: true, key: 'ArrowLeft', code: 'ArrowLeft' }), false),
    ).toBeNull();
  });
});
