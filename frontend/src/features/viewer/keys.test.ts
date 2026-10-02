import { describe, expect, it } from 'vitest';
import { isTypingTarget, type KeyPress, viewerKeyAction } from '@/features/viewer/keys';

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

  it('leaves the arrow keys to a slider, which moves its own value by them', () => {
    const target = document.createElement('span');
    target.setAttribute('role', 'slider');
    expect(viewerKeyAction(press('ArrowRight', { target }), false)).toBeNull();
    expect(viewerKeyAction(press('Home', { target }), false)).toBeNull();
  });

  it('still acts on a key pressed on a button', () => {
    const target = document.createElement('button');
    expect(viewerKeyAction(press('ArrowLeft', { target }), false)).toBe('previous');
  });
});

describe('isTypingTarget', () => {
  it.each(['input', 'textarea', 'select'])('is true for a %s', (tag) => {
    expect(isTypingTarget(document.createElement(tag))).toBe(true);
  });

  it('is false for a slider, which keeps its arrow keys but has no text to undo', () => {
    const slider = document.createElement('div');
    slider.setAttribute('role', 'slider');
    expect(isTypingTarget(slider)).toBe(false);
  });

  it('is false for a button and for nothing', () => {
    expect(isTypingTarget(document.createElement('button'))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
