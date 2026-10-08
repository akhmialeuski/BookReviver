import OpenSeadragon from 'openseadragon';
import type { ViewSize } from '@/features/place/canvas';
import { CanvasStage, HIDDEN, SHAPE_DIGITS, toRect, VISIBLE } from '@/features/viewer/canvasStage';
import {
  layoutView,
  PAGE_HEIGHT,
  pageRect,
  type ViewLayout,
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

export class ViewerStage extends CanvasStage {
  /** Image of each page that is loading or loaded, by the path of its `info.json`. */
  private readonly pending = new Map<string, Promise<OpenSeadragon.TiledImage | null>>();
  private readonly loaded = new Map<string, OpenSeadragon.TiledImage>();
  private layout: ViewLayout | null = null;

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
    const token = this.begin();
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
    if (current.size > 0) {
      const key = `${fit}:${view.map((page) => page.id).join(',')}:${layout.width.toFixed(SHAPE_DIGITS)}`;
      this.arrive(key, (immediately) => this.fit(fit, immediately), failed.length === 0);
    }

    void this.preload(around, token);
    return { layout, failed };
  }

  /** Fit the current view to the viewport. */
  fit(mode: FitMode, immediately = false): void {
    if (this.layout === null) {
      return;
    }
    const { viewport } = this.viewer;
    if (mode === FitMode.Page) {
      this.fitAboveToolbar(this.viewer, pageRect(this.layout), immediately);
    } else {
      viewport.fitBounds(toRect(widthRect(this.layout, viewport.getAspectRatio())), immediately);
    }
  }

  protected override fittedSize(): ViewSize | null {
    return this.layout;
  }

  override destroy(): void {
    super.destroy();
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
    const request = this.loadPicture(this.viewer, infoUrl).then((item) => {
      if (item === null) {
        // A pyramid that is not cut yet is tried again by the next view that asks for the page
        this.pending.delete(infoUrl);
      } else {
        this.loaded.set(infoUrl, item);
      }
      return item;
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
        this.drop(this.viewer, item);
      });
    }
  }
}
