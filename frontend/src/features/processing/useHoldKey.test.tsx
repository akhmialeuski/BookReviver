import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { type HoldKeyPress, isHoldKey, useHoldKey } from '@/features/processing/useHoldKey';

function press(overrides: Partial<HoldKeyPress> = {}): HoldKeyPress {
  return {
    key: ' ',
    code: 'Space',
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    target: document.body,
    ...overrides,
  };
}

describe('isHoldKey', () => {
  it('is Space on the page', () => {
    expect(isHoldKey(press(), false)).toBe(true);
  });

  it('is no other key', () => {
    expect(isHoldKey(press({ code: 'Enter', key: 'Enter' }), false)).toBe(false);
  });

  it('is not Space with a modifier, or while a dialog is open', () => {
    expect(isHoldKey(press({ ctrlKey: true }), false)).toBe(false);
    expect(isHoldKey(press({ shiftKey: true }), false)).toBe(false);
    expect(isHoldKey(press(), true)).toBe(false);
  });

  it('is not Space typed into a field', () => {
    expect(isHoldKey(press({ target: document.createElement('input') }), false)).toBe(false);
    expect(isHoldKey(press({ target: document.createElement('textarea') }), false)).toBe(false);
  });

  it('is not Space on a control that uses it itself', () => {
    const button = document.createElement('button');
    const slider = document.createElement('span');
    slider.setAttribute('role', 'slider');
    const inside = document.createElement('span');
    button.append(inside);

    expect(isHoldKey(press({ target: button }), false)).toBe(false);
    expect(isHoldKey(press({ target: inside }), false)).toBe(false);
    expect(isHoldKey(press({ target: slider }), false)).toBe(false);
  });
});

describe('useHoldKey', () => {
  let container: HTMLDivElement;
  let root: Root;
  const changes = vi.fn();

  function Probe({ enabled }: { enabled: boolean }): null {
    useHoldKey(enabled, changes);
    return null;
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    changes.mockClear();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function key(type: 'keydown' | 'keyup', init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent(type, {
      code: 'Space',
      key: ' ',
      bubbles: true,
      cancelable: true,
      ...init,
    });
    act(() => {
      document.body.dispatchEvent(event);
    });
    return event;
  }

  it('says the key is held while it is down and let go when it comes up', () => {
    act(() => root.render(<Probe enabled />));

    const down = key('keydown');
    expect(changes).toHaveBeenLastCalledWith(true);
    expect(down.defaultPrevented).toBe(true);

    key('keyup');
    expect(changes).toHaveBeenLastCalledWith(false);
  });

  it('says it once for a key that repeats while held', () => {
    act(() => root.render(<Probe enabled />));

    key('keydown');
    key('keydown', { repeat: true });
    key('keydown', { repeat: true });

    expect(changes.mock.calls.filter(([held]) => held === true)).toHaveLength(1);
  });

  it('lets go when the window loses the focus', () => {
    act(() => root.render(<Probe enabled />));
    key('keydown');

    act(() => {
      window.dispatchEvent(new Event('blur'));
    });

    expect(changes).toHaveBeenLastCalledWith(false);
  });

  it('does nothing while disabled', () => {
    act(() => root.render(<Probe enabled={false} />));

    const down = key('keydown');

    expect(changes).not.toHaveBeenCalled();
    expect(down.defaultPrevented).toBe(false);
  });

  it('lets go when it is disabled while the key is down', () => {
    act(() => root.render(<Probe enabled />));
    key('keydown');

    act(() => root.render(<Probe enabled={false} />));

    expect(changes).toHaveBeenLastCalledWith(false);
  });
});
