import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUndoKey } from '@/features/processing/useUndoKey';

/** Ctrl+Z (or Cmd+Z) answered while the component is mounted, unless something else took the key. */

describe('useUndoKey', () => {
  let container: HTMLDivElement;
  let root: Root;
  const undo = vi.fn();

  function Probe(): React.JSX.Element {
    useUndoKey(undo);
    return <input data-testid="field" />;
  }

  function press(init: KeyboardEventInit, target: EventTarget = window): KeyboardEvent {
    const event = new KeyboardEvent('keydown', {
      key: 'z',
      bubbles: true,
      cancelable: true,
      ...init,
    });
    act(() => {
      target.dispatchEvent(event);
    });
    return event;
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    undo.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    act(() => root.render(<Probe />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('calls the function for Ctrl+Z and for Cmd+Z, and takes the key', () => {
    const control = press({ ctrlKey: true });
    press({ metaKey: true });

    expect(undo).toHaveBeenCalledTimes(2);
    expect(control.defaultPrevented).toBe(true);
  });

  it('leaves Z alone, and Ctrl+Shift+Z and Ctrl+Alt+Z, which are other keys', () => {
    press({});
    press({ ctrlKey: true, shiftKey: true });
    press({ ctrlKey: true, altKey: true });

    expect(undo).not.toHaveBeenCalled();
  });

  it('leaves the undo of a field to the field', () => {
    const field = container.querySelector('input');
    if (field === null) {
      throw new Error('The field is not there.');
    }

    press({ ctrlKey: true }, field);

    expect(undo).not.toHaveBeenCalled();
  });

  it('leaves a key an editor already took, and a key pressed while a dialog is open', () => {
    window.addEventListener('keydown', (event) => event.preventDefault(), {
      capture: true,
      once: true,
    });
    press({ ctrlKey: true });
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    document.body.append(dialog);
    press({ ctrlKey: true });
    dialog.remove();

    expect(undo).not.toHaveBeenCalled();
  });

  it('stops answering when the component is unmounted', () => {
    act(() => root.unmount());
    root = createRoot(container);

    press({ ctrlKey: true });

    expect(undo).not.toHaveBeenCalled();
  });
});
