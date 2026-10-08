import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  binarize,
  deskew,
  deskewMethods,
  margins,
  processor,
  spread,
  whole,
} from '@/features/processing/fixtures';
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
    marked?: ReadonlySet<string>,
  ): ReturnType<typeof vi.fn> {
    act(() =>
      root.render(
        <ParamsForm processor={processor} params={params} marked={marked} onChange={onChange} />,
      ),
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

  it('names the millimetre in the label of each margin and shows the length typed in it', () => {
    render(margins(), { margin_top: 10, margin_inner: 12.5 });

    const labels = [...container.querySelectorAll('label')].map((label) => label.textContent);
    expect(labels).toEqual(['Top margin, mm', 'Inner margin, mm']);
    expect(container.textContent).toContain('in millimetres of the paper');
    const inputs = [...container.querySelectorAll<HTMLInputElement>('input[type="number"]')];
    expect(inputs.map((input) => input.value)).toEqual(['10', '12.5']);
  });

  it('hands back a margin typed with a tenth of a millimetre as it is', () => {
    const onChange = render(margins(), { margin_top: 10, margin_inner: 15 });
    const input = container.querySelector<HTMLInputElement>('input[type="number"]');

    typeInto(input as HTMLInputElement, '7.5');

    expect(onChange).toHaveBeenLastCalledWith({ margin_top: 7.5, margin_inner: 15 });
  });

  it('marks the field the open page changes for itself in its label, and only that field', () => {
    render(deskew(), { max_angle: 5, min_confidence: 0.3 }, vi.fn(), new Set(['max_angle']));

    const labels = [...container.querySelectorAll('label')].map((label) => label.textContent);
    expect(labels).toEqual(['Largest slant · changed for this page', 'Least confidence']);
  });

  it('draws no mark when the page changes nothing', () => {
    render(deskew(), { max_angle: 5, min_confidence: 0.3 }, vi.fn(), new Set());

    expect(container.textContent).not.toContain('changed for this page');
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

  it('offers the methods of a processor and shows only the fields of the method that is chosen', () => {
    render(deskewMethods(), { method: 'projection', max_angle: 5, min_confidence: 0.3 });

    const choice = container.querySelector('button[aria-haspopup="listbox"]');
    expect(choice?.textContent).toBe('Projection of the ink');
    // The name of the method is the choice, and its fields have no heading of the same words
    expect(container.querySelector('h5')).toBeNull();
    const labels = [...container.querySelectorAll('label')].map((label) => label.textContent);
    expect(labels).toEqual(expect.arrayContaining(['Largest slant', 'Least confidence']));
    expect(labels).not.toContain('Shortest line');
    expect(labels).not.toContain('Fewest lines');
    expect(container.textContent).not.toContain('projection');
    expect(container.textContent).not.toContain('DeskewParams');
  });

  it('shows the fields of the method the parameters name, and not of the first', () => {
    render(deskewMethods(), {
      method: 'hough',
      max_angle: 5,
      min_confidence: 0.3,
      min_line_share: 0.3,
    });

    const labels = [...container.querySelectorAll('label')].map((label) => label.textContent);
    expect(labels).toContain('Shortest line');
    expect(labels).not.toContain('Fewest lines');
  });

  describe('a step whose fields depend on a choice', () => {
    const SAUVOLA = {
      mode: 'bw',
      thickness: 0,
      smooth: false,
      method: 'sauvola',
      window: 41,
      k: 0.2,
    };

    function labelsOf(): (string | null)[] {
      return [...container.querySelectorAll('label')].map((label) => label.textContent);
    }

    it('shows the fields of the method that is chosen and not those of the others', () => {
      render(binarize(), SAUVOLA);

      expect(labelsOf()).toEqual(
        expect.arrayContaining(['Output', 'Stroke thickness', 'Window, px', 'Coefficient k']),
      );
      expect(labelsOf()).not.toContain('Method');
    });

    it('shows no window and no coefficient for a method that has none', () => {
      render(binarize(), { mode: 'bw', thickness: 0, smooth: false, method: 'otsu' });

      expect(labelsOf()).not.toContain('Window, px');
      expect(labelsOf()).not.toContain('Coefficient k');
      expect(container.querySelector('form')).not.toBeNull();
    });

    it('shows the window and no coefficient for a method that has only a window', () => {
      render(binarize(), { mode: 'bw', thickness: 0, smooth: false, method: 'su', window: 31 });

      expect(labelsOf()).toContain('Window, px');
      expect(labelsOf()).not.toContain('Coefficient k');
    });

    it('names the method that is chosen in the choice of the method', () => {
      render(binarize(), SAUVOLA);

      expect(container.querySelector('button[aria-haspopup="listbox"]')?.textContent).toBe(
        'Sauvola',
      );
    });

    it('draws the thickness as a slider with an input', () => {
      render(binarize(), SAUVOLA);

      const thumbs = [...container.querySelectorAll('[role="slider"]')];
      expect(thumbs.map((thumb) => thumb.getAttribute('aria-label'))).toContain('Stroke thickness');
    });
  });

  // A form hands back its defaults for a field the parameters lack, so the server lists a recipe with every field
  describe('on opening', () => {
    // The parameter model of `geometry.crop`, which refuses names it does not know
    const CROP = processor('geometry.crop', {
      parameters: {
        $defs: {
          Binarization: { enum: ['otsu', 'adaptive'], title: 'Binarization', type: 'string' },
        },
        additionalProperties: false,
        properties: {
          method: { const: 'ink-blocks', default: 'ink-blocks', title: 'Method', type: 'string' },
          binarization: { $ref: '#/$defs/Binarization', default: 'otsu', title: 'Black and white' },
          noise_min_area: {
            default: 4,
            maximum: 10000,
            minimum: 0,
            title: 'Smallest speck',
            type: 'integer',
          },
        },
        title: 'CropParams',
        type: 'object',
      },
    });
    const SAVED = { method: 'ink-blocks', binarization: 'otsu', noise_min_area: 4 };

    it.each([
      ['whole parameters', CROP, SAVED],
      ['a parameter the schema no longer has', CROP, { ...SAVED, margin_percent: 8 }],
      [
        'whole parameters of a step with methods',
        binarize(),
        { mode: 'bw', thickness: 0, smooth: false, method: 'su', window: 31 },
      ],
    ])('hands nothing back for %s', (_name, processorOf, params) => {
      const onChange = render(processorOf, params);

      expect(onChange).not.toHaveBeenCalled();
    });
  });

  it('says so for a step that has no settings', () => {
    render(whole(), {});

    expect(container.textContent).toBe('This step has no settings.');
    expect(container.querySelector('form')).toBeNull();
  });
});
