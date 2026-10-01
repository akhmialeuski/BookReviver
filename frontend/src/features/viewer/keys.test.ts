import { describe, expect, it } from 'vitest';
import { type KeyPress, viewerKeyAction } from '@/features/viewer/keys';

function press(key: string, extra: Partial<KeyPress> = {}): KeyPress {
  return {
    key,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    target: null,
    ...extra,
  };
}

describe('viewerKeyAction', () => {
  it.each([
    ['ArrowLeft', 'previous'],
    ['ArrowRight', 'next'],
    ['Home', 'first'],
    ['End', 'last'],
  ])('maps %s to %s', (key, action) => {
    expect(viewerKeyAction(press(key), false)).toBe(action);
  });

  it.each(['a', 'ArrowUp', 'ArrowDown', 'Enter', ' '])('leaves %s alone', (key) => {
    expect(viewerKeyAction(press(key), false)).toBeNull();
  });

  it.each(['altKey', 'ctrlKey', 'metaKey', 'shiftKey'] as const)(
    'leaves a key pressed with %s to the browser',
    (modifier) => {
      expect(viewerKeyAction(press('ArrowRight', { [modifier]: true }), false)).toBeNull();
    },
  );

  it('leaves the keys to an open dialog', () => {
    expect(viewerKeyAction(press('ArrowRight'), true)).toBeNull();
  });

  it.each(['input', 'textarea', 'select'])('leaves the keys typed into a %s', (tag) => {
    const target = document.createElement(tag);
    expect(viewerKeyAction(press('ArrowLeft', { target }), false)).toBeNull();
  });

  it('still acts on a key pressed on a button', () => {
    const target = document.createElement('button');
    expect(viewerKeyAction(press('ArrowLeft', { target }), false)).toBe('previous');
  });
});
