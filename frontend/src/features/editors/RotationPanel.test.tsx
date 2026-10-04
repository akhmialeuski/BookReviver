import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RotationPanel } from '@/features/editors/RotationPanel';

/**
 * The part of the rotation editor in the panel: the slider over the range the step looks in, the field, and the button that
 * lays the page level.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

describe('RotationPanel', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onChange = vi.fn();
  const onCommit = vi.fn();

  function render(
    degrees: number,
    params: Record<string, unknown> = { max_angle: 5 },
    disabled = false,
  ): void {
    act(() =>
      root.render(
        <RotationPanel
          shape={{ degrees }}
          processorKey="geometry.deskew"
          params={params}
          disabled={disabled}
          size={null}
          onChange={onChange}
          onCommit={onCommit}
        />,
      ),
    );
  }

  const thumb = (): HTMLElement | null => container.querySelector('[role="slider"]');
  const zero = (): HTMLButtonElement | null =>
    container.querySelector('[data-testid="angle-zero"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    onChange.mockReset();
    onCommit.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('runs the slider over the largest slant of the step, with both ends written under it', () => {
    render(-1.4);

    expect(thumb()?.getAttribute('aria-valuemin')).toBe('-5');
    expect(thumb()?.getAttribute('aria-valuemax')).toBe('5');
    expect(thumb()?.getAttribute('aria-valuenow')).toBe('-1.4');
    expect(container.textContent).toContain('-5°');
    expect(container.textContent).toContain('+5°');
  });

  it('runs the slider over the limit of the editor for a step that sets no largest slant', () => {
    render(0, {});

    expect(thumb()?.getAttribute('aria-valuemax')).toBe('45');
  });

  it('shows an angle outside the range at the end of the slider and keeps it in the field', () => {
    render(12);

    expect(thumb()?.getAttribute('aria-valuenow')).toBe('5');
    expect(container.querySelector('input')?.value).toBe('12');
  });

  it('turns the page by 0.05 for an arrow key on the slider, and saves it', () => {
    render(1);
    act(() => {
      thumb()?.focus();
      thumb()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });

    expect(onChange).toHaveBeenCalledWith({ degrees: 1.05 });
    expect(onCommit).toHaveBeenCalledWith({ degrees: 1.05 });
  });

  it('lays the page level with the zero button, which has nothing to do when it already is', () => {
    render(-1.4);
    act(() => zero()?.click());
    expect(onCommit).toHaveBeenCalledWith({ degrees: 0 });

    render(0);
    expect(zero()?.disabled).toBe(true);
  });

  it('cannot be changed while a change is being saved', () => {
    render(1, { max_angle: 5 }, true);

    expect(zero()?.disabled).toBe(true);
    expect(container.querySelector('input')?.disabled).toBe(true);
    expect(thumb()?.getAttribute('data-disabled')).not.toBeNull();
  });
});
