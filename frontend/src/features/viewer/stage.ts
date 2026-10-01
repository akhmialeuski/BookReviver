import OpenSeadragon from 'openseadragon';
import {
  layoutView,
  PAGE_HEIGHT,
  pageRect,
  type ViewLayout,
  type WorldRect,
  widthRect,
} from '@/features/viewer/layout';

/**
 * The OpenSeadragon viewer behind the screen, wrapped in a class that knows pages and views instead of tile sources.
 *
 * One view is the page or the two pages of a spread that the reader looks at. The stage keeps the views next to it
 * in the world too, stacked at the same place with an opacity of zero and `preload` on, which makes OpenSeadragon
 * read their `info.json` and cut tiles while they are hidden, so a page turn only changes opacities and the next
 * page is sharp at once. Pages that are no longer in the current view or its neighbours are taken out of the world.
 * A turn that arrives while the previous one is still loading wins, and the older one stops touching the world.
 */

/** How the view fills the viewport: the whole view inside it, or its width across it. */
export const FitMode = {
  Page: 'page',
  Width: 'width',
} as const;

/** One mode of fitting the view to the viewport (derived from {@link FitMode}). */
export type FitMode = (typeof FitMode)[keyof typeof FitMode];

/** A page the stage can show. */
export interface StagePage {
  id: string;
  /** Path of the `info.json` of the page's IIIF pyramid, or null for a page without an image. */
  infoUrl: string | null;
}

/** What `show` found out about the view it put on the stage. */
export interface ShownView {
  layout: ViewLayout;
  /** Ids of the pages whose tile source could not be read. */
  failed: string[];
}

const ZOOM_STEP = 1.5;
const ANIMATION_SECONDS = 0.35;
const HIDDEN = 0;
const VISIBLE = 1;

/** Pull the added image out of the object OpenSeadragon passes to the `success` callback of `addTiledImage`. */
function addedItem(event: unknown): OpenSeadragon.TiledImage | null {
  if (typeof event === 'object' && event !== null && 'item' in event) {
    return event.item as OpenSeadragon.TiledImage;
  }
  return null;
}

// OpenSeadragon asks for the tile that is a whole image at full size as `full/max/`, which an IIIF 3 server
// resolves, while the pyramid is stored as files that `dzsave` named `full/<width>,<height>/`
const WHOLE_IMAGE_SIZE = '/full/max/';

/**
 * Make a pyramid whose single tile is the whole image findable.
 *
 * The tile route serves the files as they are stored and resolves no IIIF size keywords, so a page of at most one
 * tile in each direction would otherwise stay blank. The size keyword is rewritten to the stored one, and every
 * other tile address is left as OpenSeadragon builds it.
 */
function nameWholeImageTile(item: OpenSeadragon.TiledImage): void {
  const { source } = item;
  const original = source.getTileUrl.bind(source);
  const stored = `/full/${source.dimensions.x},${source.dimensions.y}/`;
  source.getTileUrl = (level, x, y) => {
    const url = original(level, x, y);
    // OpenSeadragon allows a function that makes the address later, which an IIIF source never returns
    return typeof url === 'string' ? url.replace(WHOLE_IMAGE_SIZE, stored) : url;
  };
}

function toRect(rect: WorldRect): OpenSeadragon.Rect {
  return new OpenSeadragon.Rect(rect.x, rect.y, rect.width, rect.height);
}

export class ViewerStage {
  private readonly viewer: OpenSeadragon.Viewer;
  /** Image of each page that is loading or loaded, by the path of its `info.json`. */
  private readonly pending = new Map<string, Promise<OpenSeadragon.TiledImage | null>>();
  private readonly loaded = new Map<string, OpenSeadragon.TiledImage>();
  private layout: ViewLayout | null = null;
  private generation = 0;
  private hasFitted = false;

  /**
   * Create the viewer inside an element.
   *
   * @param element The element OpenSeadragon fills; it needs a size of its own.
   */
  constructor(element: HTMLElement) {
    this.viewer = OpenSeadragon({
      element,
      // The screen has its own buttons and its own keys, so a page turn is not an arrow key panning the image
      showNavigationControl: false,
      showNavigator: false,
      keyboardNavEnabled: false,
      animationTime: ANIMATION_SECONDS,
      visibilityRatio: 0.5,
      minZoomImageRatio: 0.5,
      maxZoomPixelRatio: 3,
      gestureSettingsMouse: { scrollToZoom: true, clickToZoom: false, dblClickToZoom: true },
      gestureSettingsTouch: { pinchToZoom: true, clickToZoom: false, dblClickToZoom: true },
    });
  }

  /**
   * Put a view on the stage and start loading the views around it.
   *
   * @param view The pages to show, left to right.
   * @param around Views to keep loaded and hidden, such as the next and the previous one.
   * @param fit How to fit the view to the viewport once it is placed.
   * @returns The layout of the view and the pages that could not be loaded, or null when a later call replaced
   * this one before it finished.
   */
  async show(
    view: readonly StagePage[],
    around: ReadonlyArray<readonly StagePage[]>,
    fit: FitMode,
  ): Promise<ShownView | null> {
    const token = ++this.generation;
    const wanted = new Set<string>();
    for (const page of [...view, ...around.flat()]) {
      if (page.infoUrl !== null) {
        wanted.add(page.infoUrl);
      }
    }
    this.evict(wanted);

    const { layout, failed } = await this.place(view, token);
    if (token !== this.generation) {
      return null;
    }
    this.layout = layout;
    const current = new Set(view.flatMap((page) => (page.infoUrl === null ? [] : [page.infoUrl])));
    for (const [url, item] of this.loaded) {
      item.setOpacity(current.has(url) ? VISIBLE : HIDDEN);
    }
    this.fit(fit, !this.hasFitted);
    this.hasFitted = true;

    void this.preload(around, token);
    return { layout, failed };
  }

  /** Fit the current view to the viewport. */
  fit(mode: FitMode, immediately = false): void {
    if (this.layout === null) {
      return;
    }
    const { viewport } = this.viewer;
    const rect =
      mode === FitMode.Page
        ? pageRect(this.layout)
        : widthRect(this.layout, viewport.getAspectRatio());
    viewport.fitBounds(toRect(rect), immediately);
  }

  /** Zoom in by one step around the centre of the viewport. */
  zoomIn(): void {
    this.viewer.viewport.zoomBy(ZOOM_STEP);
    this.viewer.viewport.applyConstraints();
  }

  /** Zoom out by one step around the centre of the viewport. */
  zoomOut(): void {
    this.viewer.viewport.zoomBy(1 / ZOOM_STEP);
    this.viewer.viewport.applyConstraints();
  }

  /** Release the viewer, its canvases and its listeners. */
  destroy(): void {
    this.generation += 1;
    this.viewer.destroy();
    this.pending.clear();
    this.loaded.clear();
  }

  /**
   * Load the pages of a view and lay them side by side.
   *
   * The positions are written only while the call that asked for them is still the latest one, because a page can
   * belong to the views of two calls, such as when the spread is switched on, and the older call must not move it.
   */
  private async place(
    view: readonly StagePage[],
    token: number,
  ): Promise<{ layout: ViewLayout; failed: string[] }> {
    const items = await Promise.all(
      view.map((page) => (page.infoUrl === null ? null : this.load(page.infoUrl))),
    );
    const failed = view.flatMap((page, index) =>
      page.infoUrl !== null && items[index] === null ? [page.id] : [],
    );
    const aspects = items.map((item) => {
      const size = item?.getContentSize();
      return size === undefined ? Number.NaN : size.x / size.y;
    });
    const layout = layoutView(aspects);
    if (token === this.generation) {
      items.forEach((item, index) => {
        const spot = layout.pages[index];
        if (item === null || spot === undefined) {
          return;
        }
        item.setHeight(PAGE_HEIGHT, true);
        item.setPosition(new OpenSeadragon.Point(spot.x, 0), true);
      });
    }
    return { layout, failed };
  }

  /** Lay out the neighbouring views hidden, unless a later `show` has taken over meanwhile. */
  private async preload(around: ReadonlyArray<readonly StagePage[]>, token: number): Promise<void> {
    for (const view of around) {
      if (token !== this.generation) {
        return;
      }
      await this.place(view, token);
    }
  }

  /** Add the pyramid of a page to the world once, hidden, and keep it for the next view that needs it. */
  private load(infoUrl: string): Promise<OpenSeadragon.TiledImage | null> {
    const known = this.pending.get(infoUrl);
    if (known !== undefined) {
      return known;
    }
    const request = new Promise<OpenSeadragon.TiledImage | null>((resolve) => {
      this.viewer.addTiledImage({
        tileSource: infoUrl,
        opacity: HIDDEN,
        preload: true,
        height: PAGE_HEIGHT,
        success: (event) => {
          const item = addedItem(event);
          if (item !== null) {
            // Before the next frame, which is the first that asks for a tile
            nameWholeImageTile(item);
            this.loaded.set(infoUrl, item);
          }
          resolve(item);
        },
        // A pyramid that is not cut yet is tried again by the next view that asks for the page
        error: () => {
          this.pending.delete(infoUrl);
          resolve(null);
        },
      });
    });
    this.pending.set(infoUrl, request);
    return request;
  }

  /** Take the pages that no view needs any more out of the world. */
  private evict(wanted: ReadonlySet<string>): void {
    for (const [url, request] of this.pending) {
      if (wanted.has(url)) {
        continue;
      }
      this.pending.delete(url);
      this.loaded.delete(url);
      void request.then((item) => {
        if (item !== null && !this.viewer.isDestroyed()) {
          this.viewer.world.removeItem(item);
        }
      });
    }
  }
}
