import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';

/** The joined buttons over radio inputs: one is chosen, the group has its legend, and a click chooses another. */

describe('SegmentedRadio', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function render(value: 'a' | 'b', onChange: (value: 'a' | 'b') => void): void {
    act(() =>
      root.render(
        <SegmentedRadio
          legend="Side"
          value={value}
          options={[
            { value: 'a', label: 'Before' },
            { value: 'b', label: 'After' },
          ]}
          onChange={onChange}
        />,
      ),
    );
  }

  it('shows the choices as radios of one group with the legend, and the chosen one checked', () => {
    render('b', () => undefined);
    const radios = container.querySelectorAll<HTMLInputElement>('input[type="radio"]');
    expect(radios).toHaveLength(2);
    expect([...radios].map((radio) => radio.checked)).toEqual([false, true]);
    expect(new Set([...radios].map((radio) => radio.name)).size).toBe(1);
    expect(container.querySelector('legend')?.textContent).toBe('Side');
    expect(container.textContent).toContain('Before');
  });

  it('hands over the value of the choice that is clicked', () => {
    const onChange = vi.fn();
    render('b', onChange);
    const first = container.querySelector<HTMLInputElement>('input[value="a"]');
    act(() => first?.click());
    expect(onChange).toHaveBeenCalledWith('a');
  });

  it('keeps the legend for screen readers only when it is hidden', () => {
    act(() =>
      root.render(
        <SegmentedRadio
          legend="Side"
          hideLegend
          value="a"
          options={[{ value: 'a', label: 'Before' }]}
          onChange={() => undefined}
        />,
      ),
    );
    expect(container.querySelector('legend')?.className).toContain('sr-only');
  });
});
