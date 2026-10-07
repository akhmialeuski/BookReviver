import { beforeEach, describe, expect, it, vi } from 'vitest';
import { type PairPlacement, Side, SourceKind } from '@/features/processing/compare';
import { CompareStage } from '@/features/processing/compareStage';
import { CompareMode } from '@/features/workspace/params';

/**
 * Where the compare stage puts its two pictures in the world: as tall as a page at the origin, or one inside the other where
 * the transform of the step places it.
 *
 * OpenSeadragon needs a canvas that jsdom does not have, so it is a stand-in whose pictures know their size in pixels and
 * keep the height and the position they are given. The pictures drawn by a real browser are checked by the end-to-end
 * scenario of the margins.
 */

const fake = vi.hoisted(() => {
  /** The size in pixels of each picture by its address. */
  const sizes = new Map<string, { x: number; y: number }>();

  class Point {
    x: number;
    y: number;
    constructor(x: number, y: number) {
      this.x = x;
      this.y = y;
    }
  }

  class Rect {
    x: number;
    y: number;
    width: number;
    height: number;
    constructor(x: number, y: number, width: number, height: number) {
      this.x = x;
      this.y = y;
      this.width = width;
      this.height = height;
    }
  }

  class Item {
    height = 1;
    position = new Point(0, 0);
    opacity = 0;
    readonly size: { x: number; y: number };
    constructor(size: { x: number; y: number }) {
      this.size = size;
    }
    setHeight(height: number): void {
      this.height = height;
    }
    setPosition(position: Point): void {
      this.position = position;
    }
    setOpacity(opacity: number): void {
      this.opacity = opacity;
    }
    setClip = vi.fn();
    getContentSize(): { x: number; y: number } {
      return this.size;
    }
    getBounds(): Rect {
      return new Rect(
        this.position.x,
        this.position.y,
        (this.height * this.size.x) / this.size.y,
        this.height,
      );
    }
  }

  const viewers: unknown[] = [];

  function viewer() {
    const items: Item[] = [];
    const made = {
      items,
      world: {
        getItemCount: () => items.length,
        setItemIndex: vi.fn(),
        removeItem: vi.fn(),
      },
      viewport: {
        getContainerSize: () => new Point(1000, 800),
        fitBounds: vi.fn(),
        getBounds: () => new Rect(0, 0, 1, 1),
        getAspectRatio: () => 1.25,
        getZoom: () => 1,
        getCenter: () => new Point(0.5, 0.5),
        pointFromPixel: (point: Point) => point,
        zoomTo: vi.fn(),
        panTo: vi.fn(),
        applyConstraints: vi.fn(),
      },
      addHandler: vi.fn(),
      addTiledImage: (options: {
        tileSource: { url: string };
        success: (event: { item: Item }) => void;
      }) => {
        const item = new Item(sizes.get(options.tileSource.url) ?? { x: 100, y: 100 });
        items.push(item);
        options.success({ item });
      },
      forceResize: vi.fn(),
      isDestroyed: () => false,
      destroy: vi.fn(),
    };
    viewers.push(made);
    return made;
  }

  return { sizes, Point, Rect, viewer, viewers };
});

vi.mock('openseadragon', () => ({
  default: Object.assign(fake.viewer, { Point: fake.Point, Rect: fake.Rect }),
}));

const BEFORE = { kind: SourceKind.Image, url: '/before.png' } as const;
const AFTER = { kind: SourceKind.Image, url: '/after.png' } as const;
const WIDTH = 1600;
const HEIGHT = 2800;

describe('CompareStage', () => {
  let element: HTMLDivElement;
  let stage: CompareStage;

  const picture = (index: number) =>
    (fake.viewers[0] as { items: unknown[] }).items[index] as {
      height: number;
      position: { x: number; y: number };
      getBounds: () => { x: number; y: number; width: number; height: number };
    };

  beforeEach(() => {
    fake.viewers.length = 0;
    fake.sizes.clear();
    // The block of 1276 by 2645 pixels, and the page of the margins
    fake.sizes.set(BEFORE.url, { x: 1276, y: 2645 });
    fake.sizes.set(AFTER.url, { x: WIDTH, y: HEIGHT });
    element = document.createElement('div');
    stage = new CompareStage(element, document.createElement('div'));
  });

  it('draws both pictures as tall as a page at the origin when there is no place for them', async () => {
    await stage.show(BEFORE, AFTER, 'p1');

    // The picture after is added first, then the picture before
    for (const index of [0, 1]) {
      expect(picture(index).height).toBe(1);
      expect(picture(index).position).toMatchObject({ x: 0, y: 0 });
    }
  });

  it('draws the page of the margins whole and the block inside it where the transform puts it', async () => {
    const placement: PairPlacement = {
      base: Side.After,
      left: 162 / WIDTH,
      top: 60 / HEIGHT,
      width: 1276 / WIDTH,
      height: 2645 / HEIGHT,
    };
    await stage.show(BEFORE, AFTER, 'p1', placement);

    const page = picture(0).getBounds();
    const block = picture(1).getBounds();
    expect(page).toMatchObject({ x: 0, y: 0, height: 1 });
    expect(block.x).toBeCloseTo((162 / WIDTH) * page.width);
    expect(block.y).toBeCloseTo(60 / HEIGHT);
    expect(block.height).toBeCloseTo(2645 / HEIGHT);
    // The block is drawn at the scale of the page: its width over its height is that of its pixels
    expect(block.width / block.height).toBeCloseTo(1276 / 2645);
    expect(element.dataset.afterBox).toBe('0.0000,0.0000,0.5714,1.0000');
  });

  it('draws the input whole and the result inside it for a page that was cut', async () => {
    fake.sizes.set(BEFORE.url, { x: 1695, y: 2795 });
    fake.sizes.set(AFTER.url, { x: 1277, y: 2645 });
    await stage.show(BEFORE, AFTER, 'p1', {
      base: Side.Before,
      left: 237 / 1695,
      top: 150 / 2795,
      width: 1277 / 1695,
      height: 2645 / 2795,
    });

    const input = picture(1).getBounds();
    const result = picture(0).getBounds();
    expect(input).toMatchObject({ x: 0, y: 0, height: 1 });
    expect(result.x).toBeCloseTo((237 / 1695) * input.width);
    expect(result.y).toBeCloseTo(150 / 2795);
    expect(result.height).toBeCloseTo(2645 / 2795);
  });

  describe('fitted to hold what the editor draws beyond the page', () => {
    // The picture after is 1600 by 2800 pixels, so it is 0.5714 wide and one tall in the world
    const BORDER = { left: -160, top: -280, width: 1920, height: 3360 };

    const lastFit = (): { x: number; y: number; width: number; height: number } => {
      const [first] = fake.viewers as {
        viewport: {
          fitBounds: {
            mock: { lastCall?: [{ x: number; y: number; width: number; height: number }] };
          };
        };
      }[];
      const call = first?.viewport.fitBounds.mock.lastCall;
      if (call === undefined) {
        throw new Error('the view was not fitted');
      }
      return call[0];
    };

    it('holds the border that lies beyond the picture on every side, with the room for its handles', async () => {
      await stage.show(null, AFTER, 'p1');
      stage.setPadding(0.08);
      stage.setReach({ rect: BORDER, size: { width: WIDTH, height: HEIGHT } });

      const fit = lastFit();
      const aspect = WIDTH / HEIGHT;
      // The border reaches a tenth of the page past the picture on every side, and the room for the handles lies beyond it
      expect(fit.x).toBeCloseTo(-0.1 * aspect - 0.08);
      expect(fit.y).toBeCloseTo(-0.1 - 0.08);
      expect(fit.x + fit.width).toBeGreaterThanOrEqual(aspect * 1.1 + 0.08 - 1e-9);
      expect(fit.y + fit.height).toBeGreaterThanOrEqual(1.1 + 0.08 - 1e-9);
    });

    it('fits the picture alone while the editor draws nothing beyond it', async () => {
      await stage.show(null, AFTER, 'p1');
      stage.setPadding(0.08);

      expect(lastFit().x).toBeCloseTo(-0.08);
    });

    it('leaves a view the reader zoomed where it is when the editor makes room round the page', async () => {
      await stage.show(null, AFTER, 'p1');
      const [first] = fake.viewers as { addHandler: { mock: { calls: [string, () => void][] } } }[];
      const scroll = first?.addHandler.mock.calls.find(([name]) => name === 'canvas-scroll');
      scroll?.[1]();
      const before = lastFit();
      stage.setPadding(0.08);

      expect(lastFit()).toBe(before);
    });

    it('leaves a view the reader moved where it is when the border changes', async () => {
      await stage.show(null, AFTER, 'p1');
      stage.setPadding(0.08);
      const [first] = fake.viewers as { addHandler: { mock: { calls: [string, () => void][] } } }[];
      const drag = first?.addHandler.mock.calls.find(([name]) => name === 'canvas-drag');
      drag?.[1]();
      const before = lastFit();
      stage.setReach({ rect: BORDER, size: { width: WIDTH, height: HEIGHT } });

      expect(lastFit()).toBe(before);
    });
  });

  describe('the place of the reader', () => {
    const PLACE = { zoom: 0.67, centre_x: 0.3, centre_y: 0.5 };

    it('is restored on the first picture and not on a view with none, so the rows that are still read do not spend it', async () => {
      const restore = vi.fn(() => PLACE);
      stage = new CompareStage(element, document.createElement('div'), { restore });

      await stage.show(null, null, 'p1');
      expect(restore).not.toHaveBeenCalled();

      await stage.show(BEFORE, AFTER, 'p1');
      expect(restore).toHaveBeenCalledTimes(1);
      // The stage of this test is the second viewer, the first being the one the other tests share
      const restored = fake.viewers.at(-1) as {
        viewport: { zoomTo: { mock: { calls: unknown[][] } } };
      };
      expect(restored.viewport.zoomTo.mock.calls).toHaveLength(1);
    });

    it('keeps the zoom of the reader across a view with no picture when the same pages come back', async () => {
      await stage.show(BEFORE, AFTER, 'p1');
      const [first] = fake.viewers as {
        viewport: { fitBounds: { mock: { calls: unknown[][] } } };
      }[];
      const fitted = first?.viewport.fitBounds.mock.calls.length;

      await stage.show(null, null, 'p1');
      await stage.show(BEFORE, AFTER, 'p1');

      expect(first?.viewport.fitBounds.mock.calls).toHaveLength(fitted ?? -1);
    });
  });

  it('keeps the picture after in the second viewer where it stands in the first, in the side by side mode', async () => {
    await stage.show(BEFORE, AFTER, 'p1', {
      base: Side.After,
      left: 0.1,
      top: 0.05,
      width: 0.8,
      height: 0.9,
    });
    stage.setMode(CompareMode.Side);
    await vi.waitFor(() => expect(fake.viewers).toHaveLength(2));
    await vi.waitFor(() => expect((fake.viewers[1] as { items: unknown[] }).items).toHaveLength(1));

    const [, aside] = fake.viewers as { items: { getBounds: () => unknown }[] }[];
    expect(aside?.items[0]?.getBounds()).toEqual(picture(0).getBounds());
  });
});
