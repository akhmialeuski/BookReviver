import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AngleField } from '@/features/editors/AngleField';

/** The field of the angle: what it shows, and when what was typed is read and saved. */

describe('AngleField', () => {
  let container: HTMLDivElement;
  let root: Root;
  const commit = vi.fn();

  function render(degrees = -2.4, disabled = false): void {
    act(() => root.render(<AngleField degrees={degrees} disabled={disabled} onCommit={commit} />));
  }

  const input = (): HTMLInputElement => {
    const found = container.querySelector('input');
    if (found === null) {
      throw new Error('The field is not there.');
    }
    return found;
  };

  function type(text: string, key: string | null): void {
    act(() => {
      input().focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(
        input(),
        text,
      );
      input().dispatchEvent(new Event('input', { bubbles: true }));
    });
    if (key !== null) {
      act(() => {
        input().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      });
    }
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    commit.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('shows the angle to a tenth of a degree', () => {
    render(-2.4);

    expect(input().value).toBe('-2.4');
    expect(input().getAttribute('aria-label')).toBe('Angle in degrees');
  });

  it('saves the typed angle when Enter leaves the field', () => {
    render();

    type('3.26', 'Enter');

    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith(3.3);
  });

  it('saves the typed angle when the focus goes elsewhere, and reads a comma', () => {
    render();

    type('1,5', null);
    act(() => input().blur());

    expect(commit).toHaveBeenCalledWith(1.5);
  });

  it('holds the angle to the limit', () => {
    render();

    type('90', 'Enter');

    expect(commit).toHaveBeenCalledWith(45);
  });

  it('saves nothing for text that is not a number, and shows the angle it had', () => {
    render();

    type('abc', 'Enter');

    expect(commit).not.toHaveBeenCalled();
    expect(input().value).toBe('-2.4');
  });

  it('saves nothing for the angle it already has', () => {
    render();

    type('-2.4', 'Enter');

    expect(commit).not.toHaveBeenCalled();
  });

  it('drops what was typed on Escape', () => {
    render();

    type('7', 'Escape');

    expect(commit).not.toHaveBeenCalled();
    expect(input().value).toBe('-2.4');
  });

  it('cannot be changed while a change is being saved', () => {
    render(1, true);

    expect(input().disabled).toBe(true);
  });
});
