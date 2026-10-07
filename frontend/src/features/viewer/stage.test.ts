import { beforeEach, describe, expect, it, type Mock, vi } from 'vitest';
import type { CanvasPositionSchema } from '@/api';
import { FitMode, ViewerStage } from '@/features/viewer/stage';

/**
 * Where the stage of the pages puts the canvas: the place the reader left is restored on the first view that has a
 * picture, and a view with none, such as pages whose rows are still being read, neither spends that place nor fits.
 *
 * OpenSeadragon needs a canvas that jsdom does not have, so it is a stand-in whose pictures know their size and keep the
 * height and the position they are given.
 */

const fake = vi.hoisted(() => {
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
    source = { dimensions: new Point(100, 100), getTileUrl: () => '' };
    setHeight = vi.fn();
    setPosition = vi.fn();
    setOpacity = vi.fn();
    getContentSize(): Point {
      return new Point(100, 100);
    }
  }

  interface FakeViewer {
    world: { removeItem: Mock };
    viewport: {
      getContainerSize: () => Point;
      fitBounds: Mock;
      getAspectRatio: () => number;
      getZoom: () => number;
      getCenter: () => Point;
      zoomTo: Mock;
      panTo: Mock;
      applyConstraints: Mock;
    };
    addHandler: Mock;
    addTiledImage: (options: { success: (event: { item: Item }) => void }) => void;
    isDestroyed: () => boolean;
    destroy: Mock;
  }

  const viewers: FakeViewer[] = [];

  function viewer(): FakeViewer {
    const made: FakeViewer = {
      world: { removeItem: vi.fn() },
      viewport: {
        getContainerSize: () => new Point(1000, 800),
        fitBounds: vi.fn(),
        getAspectRatio: () => 1.25,
        getZoom: () => 1,
        getCenter: () => new Point(0.5, 0.5),
        zoomTo: vi.fn(),
        panTo: vi.fn(),
        applyConstraints: vi.fn(),
      },
      addHandler: vi.fn(),
      addTiledImage: (options: { success: (event: { item: Item }) => void }) => {
        options.success({ item: new Item() });
      },
      isDestroyed: () => false,
      destroy: vi.fn(),
    };
    viewers.push(made);
    return made;
  }

  return { Point, Rect, viewer, viewers };
});

vi.mock('openseadragon', () => ({
  default: Object.assign(fake.viewer, { Point: fake.Point, Rect: fake.Rect }),
}));

const PLACE = { zoom: 0.67, centre_x: 0.3, centre_y: 0.5 };
const PICTURED = [{ id: 'p1', infoUrl: '/p1/info.json' }];
const UNPICTURED = [{ id: 'p1', infoUrl: null }];

describe('ViewerStage', () => {
  let restore: ReturnType<typeof vi.fn<() => CanvasPositionSchema | null>>;
  let stage: ViewerStage;

  const viewport = () => fake.viewers[0]?.viewport;

  beforeEach(() => {
    fake.viewers.length = 0;
    restore = vi.fn(() => PLACE);
    stage = new ViewerStage(document.createElement('div'), { restore });
  });

  it('restores the place of the reader on the first view that has a picture', async () => {
    await stage.show(PICTURED, [], FitMode.Page);

    expect(restore).toHaveBeenCalledTimes(1);
    expect(viewport()?.zoomTo).toHaveBeenCalledTimes(1);
    expect(viewport()?.fitBounds).not.toHaveBeenCalled();
  });

  it('leaves the place to be restored while the pages of the view have no picture yet', async () => {
    await stage.show(UNPICTURED, [], FitMode.Page);

    expect(restore).not.toHaveBeenCalled();
    expect(viewport()?.fitBounds).not.toHaveBeenCalled();
    // A position is not told for a canvas that shows nothing
    expect(stage.readView()).toBeNull();

    await stage.show(PICTURED, [], FitMode.Page);

    expect(restore).toHaveBeenCalledTimes(1);
    expect(viewport()?.zoomTo).toHaveBeenCalledTimes(1);
  });

  it('keeps the zoom of the reader across a view with no picture when the same page comes back', async () => {
    restore.mockReturnValue(null);
    await stage.show(PICTURED, [], FitMode.Page);
    const fitted = viewport()?.fitBounds.mock.calls.length;

    await stage.show(UNPICTURED, [], FitMode.Page);
    await stage.show(PICTURED, [], FitMode.Page);

    expect(viewport()?.fitBounds.mock.calls).toHaveLength(fitted ?? -1);
  });
});
