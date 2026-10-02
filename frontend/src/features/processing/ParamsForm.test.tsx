import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, spread, whole } from '@/features/processing/fixtures';
import { ParamsForm } from '@/features/processing/ParamsForm';

/**
 * The form of a step is drawn from the JSON Schema the real plugins give, so these tests draw the forms of the two
 * plugins the stages have today: labels from the titles, hints from the descriptions, a slider with an input for a
 * number with both bounds, and the values of the form handed back as they are typed.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

describe('ParamsForm', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function render(
    processor: Parameters<typeof ParamsForm>[0]['processor'],
    params: Record<string, unknown>,
    onChange = vi.fn(),
  ): ReturnType<typeof vi.fn> {
    act(() =>
      root.render(<ParamsForm processor={processor} params={params} onChange={onChange} />),
    );
    return onChange;
  }

  function typeInto(input: HTMLInputElement, value: string): void {
    // React tracks the value of an input, so the native setter is used to make the change a real one
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    act(() => {
      setter?.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('labels each field with its title and not with its name', () => {
    render(deskew(), { max_angle: 5, min_confidence: 0.3 });

    const labels = [...container.querySelectorAll('label')].map((label) => label.textContent);
    expect(labels).toEqual(['Largest slant', 'Least confidence']);
    expect(container.textContent).not.toContain('max_angle');
    expect(container.textContent).not.toContain('DeskewParams');
  });

  it('puts the description of a field under it as a hint', () => {
    render(deskew(), { max_angle: 5, min_confidence: 0.3 });

    expect(container.textContent).toContain('Largest angle in degrees to look for, each way');
    expect(container.textContent).not.toContain(':ivar');
  });

  it('draws a number with both bounds as a slider with an input', () => {
    render(deskew(), { max_angle: 5, min_confidence: 0.3 });

    expect(container.querySelectorAll('[role="slider"]')).toHaveLength(2);
    expect(container.querySelectorAll('input[type="number"]')).toHaveLength(2);
    const thumbs = [...container.querySelectorAll('[role="slider"]')];
    expect(thumbs.map((thumb) => thumb.getAttribute('aria-label'))).toEqual([
      'Largest slant',
      'Least confidence',
    ]);
  });

  it('draws a number with one bound as an input alone', () => {
    render(spread(), { search_band: 0.3, overlap_px: 0, min_confidence: 0.1 });

    // Two of the three fields have both bounds
    expect(container.querySelectorAll('[role="slider"]')).toHaveLength(2);
    expect(container.querySelectorAll('input[type="number"]')).toHaveLength(3);
  });

  it('hands back the values with the one typed changed', () => {
    const onChange = render(deskew(), { max_angle: 5, min_confidence: 0.3 });

    const [first] = container.querySelectorAll<HTMLInputElement>('input[type="number"]');
    typeInto(first as HTMLInputElement, '7.5');

    expect(onChange).toHaveBeenLastCalledWith({ max_angle: 7.5, min_confidence: 0.3 });
  });

  it('keeps a value out of its bounds as typed and flags it', () => {
    const onChange = render(deskew(), { max_angle: 5, min_confidence: 0.3 });

    const [first] = container.querySelectorAll<HTMLInputElement>('input[type="number"]');
    typeInto(first as HTMLInputElement, '99');

    expect(onChange).toHaveBeenLastCalledWith({ max_angle: 99, min_confidence: 0.3 });
    expect(first?.getAttribute('aria-invalid')).toBe('true');
  });

  it('says so for a step that has no settings', () => {
    render(whole(), {});

    expect(container.textContent).toBe('This step has no settings.');
    expect(container.querySelector('form')).toBeNull();
  });
});
