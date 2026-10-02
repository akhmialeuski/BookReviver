import OpenSeadragon from 'openseadragon';
import { describe, expect, it } from 'vitest';
import { SceneMapping } from '@/features/editors/mapping';

/**
 * The conversion of an editor's points between the image and the screen, checked against a real OpenSeadragon viewport
 * at several zooms, pans and rotations of the view.
 */

const CONTAINER = { width: 800, height: 600 };
const SCAN = { width: 2000, height: 3000 };
const CENTRE = { x: 0.5, y: 0.75 };
const PRECISION = 3;

/** The view a test puts the viewport in. */
interface Where {
  zoom: number;
  pan: { x: number; y: number };
  rotation: number;
}

/** A viewport over one picture of the given size, put in a view without waiting for an animation. */
function viewportOver(
  size: { width: number; height: number },
  where: Partial<Where> = {},
): OpenSeadragon.Viewport {
  const view = new OpenSeadragon.Viewport({
    containerSize: new OpenSeadragon.Point(CONTAINER.width, CONTAINER.height),
    degrees: where.rotation ?? 0,
    minZoomImageRatio: 0.01,
    maxZoomPixelRatio: 100,
    visibilityRatio: 0,
  });
  view.resetContentSize(new OpenSeadragon.Point(size.width, size.height));
  const pan = where.pan ?? CENTRE;
  view.zoomTo(where.zoom ?? 1, undefined, true);
  view.panTo(new OpenSeadragon.Point(pan.x, pan.y), true);
  return view;
}

function mappingOf(where: Partial<Where> = {}): SceneMapping {
  return new SceneMapping(viewportOver(SCAN, where), SCAN, SCAN);
}

function angleOf(
  mapping: SceneMapping,
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  const a = mapping.toScreen(from);
  const b = mapping.toScreen(to);
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
}

describe('SceneMapping', () => {
  it('draws the image in its own proportions', () => {
    const mapping = mappingOf();

    const topLeft = mapping.toScreen({ x: 0, y: 0 });
    const bottomRight = mapping.toScreen({ x: SCAN.width, y: SCAN.height });

    expect((bottomRight.x - topLeft.x) / (bottomRight.y - topLeft.y)).toBeCloseTo(
      SCAN.width / SCAN.height,
      PRECISION,
    );
  });

  it.each([
    ['at the starting view', {}],
    ['zoomed in', { zoom: 4 }],
    ['zoomed out', { zoom: 0.25 }],
    ['panned to a corner', { zoom: 3, pan: { x: 0.1, y: 0.2 } }],
    ['turned a quarter', { rotation: 90 }],
    ['turned a little, zoomed and panned', { zoom: 2.5, pan: { x: 0.3, y: 0.6 }, rotation: 17.5 }],
  ])('gives the same point back after a round trip %s', (_name, where) => {
    const mapping = mappingOf(where);
    const point = { x: 1234, y: 567 };

    const back = mapping.toImage(mapping.toScreen(point));

    expect(back.x).toBeCloseTo(point.x, PRECISION);
    expect(back.y).toBeCloseTo(point.y, PRECISION);
  });

  it('scales the picture by the zoom around the middle of the container', () => {
    const fitted = mappingOf({ zoom: 1 });
    const zoomed = mappingOf({ zoom: 2 });
    const middle = { x: SCAN.width * CENTRE.x, y: SCAN.height * (CENTRE.y / 1.5) };

    expect(zoomed.pixelLength()).toBeCloseTo(fitted.pixelLength() * 2, PRECISION);
    // The point at the middle of the view stays at the middle of the container when the zoom changes
    const before = fitted.toScreen(middle);
    const after = zoomed.toScreen(middle);
    expect(after.x).toBeCloseTo(before.x, PRECISION);
    expect(after.y).toBeCloseTo(before.y, PRECISION);
  });

  it('moves the picture against the pan', () => {
    const start = mappingOf({ pan: { x: 0.5, y: 0.75 } });
    const panned = mappingOf({ pan: { x: 0.6, y: 0.75 } });
    const point = { x: 1000, y: 1000 };

    const from = start.toScreen(point);
    const to = panned.toScreen(point);

    // Panning right by a tenth of the image's width moves the picture left by the same share of the container
    expect(from.x - to.x).toBeCloseTo(0.1 * start.pixelLength() * SCAN.width, 1);
    expect(to.y).toBeCloseTo(from.y, PRECISION);
  });

  it('turns the picture with the view without changing its size', () => {
    const a = { x: 200, y: 300 };
    const b = { x: 1700, y: 2600 };
    const straight = mappingOf({ zoom: 1.5 });
    const turned = mappingOf({ zoom: 1.5, rotation: 33 });
    const gap = (mapping: SceneMapping): number => {
      const from = mapping.toScreen(a);
      const to = mapping.toScreen(b);
      return Math.hypot(to.x - from.x, to.y - from.y);
    };

    expect(gap(turned)).toBeCloseTo(gap(straight), PRECISION);
    expect(angleOf(turned, a, b) - angleOf(straight, a, b)).toBeCloseTo(33, PRECISION);
    expect(turned.pixelLength()).toBeCloseTo(straight.pixelLength(), PRECISION);
  });

  it('converts between the pixels of an edit and those of a reduced picture', () => {
    const reduced = { width: 500, height: 750 };
    const view = viewportOver(reduced, { zoom: 2, pan: { x: 0.4, y: 0.7 } });
    const mapping = new SceneMapping(view, SCAN, reduced);
    const point = { x: 800, y: 1200 };

    const screen = mapping.toScreen(point);
    const direct = view.imageToViewerElementCoordinates(new OpenSeadragon.Point(200, 300));

    expect(screen.x).toBeCloseTo(direct.x, PRECISION);
    expect(screen.y).toBeCloseTo(direct.y, PRECISION);
    const back = mapping.toImage(screen);
    expect(back.x).toBeCloseTo(point.x, PRECISION);
    expect(back.y).toBeCloseTo(point.y, PRECISION);
  });
});
