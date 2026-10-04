import { describe, expect, it } from 'vitest';
import {
  FALLBACK_ASPECT,
  layoutView,
  pageRect,
  SPREAD_GUTTER,
  widthRect,
  withBottomInset,
} from '@/features/viewer/layout';

describe('layoutView', () => {
  it('places one page at the origin', () => {
    const layout = layoutView([0.5]);
    expect(layout.pages).toEqual([{ x: 0, width: 0.5 }]);
    expect(layout.width).toBe(0.5);
    expect(layout.height).toBe(1);
  });

  it('places the second page after the first with a gutter', () => {
    const layout = layoutView([0.5, 0.75]);
    expect(layout.pages[1]).toEqual({ x: 0.5 + SPREAD_GUTTER, width: 0.75 });
    expect(layout.width).toBeCloseTo(0.5 + SPREAD_GUTTER + 0.75);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])(
    'gives a page of aspect %j the fallback width',
    (aspect) => {
      expect(layoutView([aspect]).pages[0]?.width).toBe(FALLBACK_ASPECT);
    },
  );

  it('lays out no pages as an empty view', () => {
    expect(layoutView([])).toEqual({ pages: [], width: 0, height: 1 });
  });
});

describe('pageRect and widthRect', () => {
  const layout = layoutView([0.5, 0.5]);

  it('fits the whole view for a page', () => {
    expect(pageRect(layout)).toEqual({ x: 0, y: 0, width: layout.width, height: 1 });
  });

  it('gives the width rectangle the shape of the viewport', () => {
    const rect = widthRect(layout, 2);
    expect(rect.width).toBe(layout.width);
    expect(rect.height).toBeCloseTo(layout.width / 2);
    expect(rect.y).toBe(0);
  });

  it('treats a viewport of no size as square', () => {
    expect(widthRect(layout, 0).height).toBe(layout.width);
  });
});

describe('withBottomInset', () => {
  const rect = { x: 0, y: 0, width: 0.7, height: 1 };

  it('leaves room below a page that is fitted by its height, so it ends the inset above the viewport bottom', () => {
    const viewport = { width: 1000, height: 800 };
    const grown = withBottomInset(rect, viewport, 64);
    // The page spans 736 px at the zoom of the fit, so the whole rectangle spans the 800 px of the viewport
    const scale = viewport.height / grown.height;

    expect(rect.height * scale).toBeCloseTo(viewport.height - 64);
  });

  it('leaves room below a page that is fitted by its width, as far as the width allows', () => {
    const viewport = { width: 350, height: 1200 };
    const grown = withBottomInset(rect, viewport, 64);
    const scale = viewport.width / rect.width;

    expect(grown.height).toBeCloseTo(rect.height + 64 / scale);
    expect(grown.height * scale).toBeLessThanOrEqual(viewport.height);
  });

  it('keeps the top and the sides of the rectangle where they were', () => {
    expect(withBottomInset(rect, { width: 1000, height: 800 }, 64)).toMatchObject({
      x: 0,
      y: 0,
      width: 0.7,
    });
  });

  it('gives the rectangle back when the viewport is no taller than the inset', () => {
    expect(withBottomInset(rect, { width: 1000, height: 64 }, 64)).toEqual(rect);
    expect(withBottomInset(rect, { width: 0, height: 800 }, 64)).toEqual(rect);
  });
});
