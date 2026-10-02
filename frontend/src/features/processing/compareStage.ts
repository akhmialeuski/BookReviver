import OpenSeadragon from 'openseadragon';
import type { CanvasPositionSchema } from '@/api';
import { fittedWidth, positionOf, viewportZoom } from '@/features/place/canvas';
import {
  type ComparePair,
  clipWidth,
  type ImageSource,
  SourceKind,
} from '@/features/processing/compare';
import { PAGE_HEIGHT } from '@/features/viewer/layout';
import { addedItem, nameWholeImageTile, type StageHooks } from '@/features/viewer/stage';
import { CompareMode } from '@/features/workspace/params';

/**
 * The OpenSeadragon canvas of the before-and-after compare, which holds the two pictures of a page and shows them as the
 * reader asks: the picture after alone, a swipe, side by side, or the picture before while a key is held.
 *
 * The swipe is two pictures in one world, the one before drawn over the one after and clipped by `TiledImage.setClip`
 * to the part left of a divider, so the zoom and the pan are shared by construction. The divider stands at a place of
 * the screen, so the clip is worked out again whenever the view moves. Side by side is two viewers, the picture before in
 * the first and the picture after in the second, whose zoom and pan follow each other. Both pictures are loaded once and
 * only their opacity and clip change when the mode does, so a switch is instant and a held key shows the picture before
 * without a wait.
 */

const ANIMATION_SECONDS = 0.35;
const HIDDEN = 0;
const VISIBLE = 1;
/** The least a clip may uncover, since a clip of no width is read as no clip at all. */
const LEAST_CLIP_PX = 0.5;

/** What `show` found out about the pictures it put on the stage. */
export interface ShownCompare {
  /** Which sides could not be read, `before` or `after`. */
  failed: string[];
}

/** The settings both viewers share. */
const VIEWER_OPTIONS = {
  showNavigationControl: false,
  showNavigator: false,
  keyboardNavEnabled: false,
  animationTime: ANIMATION_SECONDS,
  visibilityRatio: 0.5,
  minZoomImageRatio: 0.5,
  maxZoomPixelRatio: 3,
  gestureSettingsMouse: { scrollToZoom: true, clickToZoom: false, dblClickToZoom: true },
  gestureSettingsTouch: { pinchToZoom: true, clickToZoom: false, dblClickToZoom: true },
};

function sameSource(a: ImageSource | null, b: ImageSource | null): boolean {
  return a === b || (a !== null && b !== null && a.kind === b.kind && a.url === b.url);
}

function tileSourceOf(source: ImageSource): string | { type: string; url: string } {
  return source.kind === SourceKind.Iiif ? source.url : { type: 'image', url: source.url };
}

export class CompareStage {
  private readonly first: OpenSeadragon.Viewer;
  private readonly secondElement: HTMLElement;
  private second: OpenSeadragon.Viewer | null = null;
  private before: OpenSeadragon.TiledImage | null = null;
  private after: OpenSeadragon.TiledImage | null = null;
  private afterAside: OpenSeadragon.TiledImage | null = null;
  private shown: ComparePair = { before: null, after: null };
  private mode: CompareMode = CompareMode.Off;
  private holding = false;
  private divider = 0.5;
  private generation = 0;
  private syncing = false;
  private hasFitted = false;
  /** Whether the pictures of the latest `show` are on the stage, so the position of the canvas belongs to them. */
  private settled = false;
  private padding = 0;
  private readonly element: HTMLElement;
  private readonly hooks: StageHooks;

  /**
   * Create the viewers inside two elements.
   *
   * @param element The element the first viewer fills, where the picture before and the swipe are drawn.
   * @param aside The element the second viewer fills, which holds the picture after in the side by side mode.
   */
  constructor(element: HTMLElement, aside: HTMLElement, hooks: StageHooks = {}) {
    this.element = element;
    this.secondElement = aside;
    this.hooks = hooks;
    this.first = OpenSeadragon({ element, ...VIEWER_OPTIONS });
    this.first.addHandler('animation', () => this.follow(this.first, this.second));
    this.first.addHandler('resize', () => this.follow(this.first, this.second));
    this.first.addHandler('animation-finish', () => {
      this.publishZoom();
      this.hooks.onViewChange?.();
    });
  }

  /** The first viewer, which a layer drawn over the canvas follows. */
  get viewer(): OpenSeadragon.Viewer {
    return this.first;
  }

  /** The picture an editor lies on: the one after, else the one before. */
  get image(): OpenSeadragon.TiledImage | null {
    return this.after ?? this.before;
  }

  /**
   * Leave room round the page when it is fitted, such as for the labels an editor puts above it.
   *
   * @param share The room on each side as a share of the height of the page.
   */
  setPadding(share: number): void {
    if (share !== this.padding) {
      this.padding = share;
      this.fit(true);
    }
  }

  /**
   * Put the two pictures of a page on the stage.
   *
   * @param before The picture before the stage, or null when there is none.
   * @param after The picture after, or null when there is none.
   * @returns What could not be read, or null when a later call replaced this one before it finished.
   */
  async show(before: ImageSource | null, after: ImageSource | null): Promise<ShownCompare | null> {
    const token = ++this.generation;
    this.settled = false;
    if (!sameSource(this.shown.before, before)) {
      this.drop(this.first, this.before);
      this.before = null;
    }
    if (!sameSource(this.shown.after, after)) {
      this.drop(this.first, this.after);
      this.drop(this.second, this.afterAside);
      this.after = null;
      this.afterAside = null;
    }
    this.shown = { before, after };

    const [loadedAfter, loadedBefore] = await Promise.all([
      this.after ?? (after === null ? null : this.load(this.first, after)),
      this.before ?? (before === null ? null : this.load(this.first, before)),
    ]);
    if (token !== this.generation) {
      return null;
    }
    this.after = loadedAfter;
    this.before = loadedBefore;
    for (const item of [this.after, this.before]) {
      item?.setHeight(PAGE_HEIGHT, true);
      item?.setPosition(new OpenSeadragon.Point(0, 0), true);
    }
    // The picture before is drawn over the picture after, which a clip then uncovers
    if (this.before !== null) {
      this.first.world.setItemIndex(this.before, this.first.world.getItemCount() - 1);
    }
    await this.placeAside(after, token);
    this.arrange();
    // The first pictures of a screen go back to where the reader left the canvas, and later ones are fitted
    const restored = this.hasFitted ? null : (this.hooks.restore?.() ?? null);
    if (restored === null) {
      this.fit(!this.hasFitted);
    } else {
      this.look(restored);
    }
    this.hasFitted = true;
    this.settled = true;
    return {
      failed: [
        ...(before !== null && this.before === null ? ['before'] : []),
        ...(after !== null && this.after === null ? ['after'] : []),
      ],
    };
  }

  /** Show the pictures another way. */
  setMode(mode: CompareMode): void {
    if (mode === this.mode) {
      return;
    }
    const wasSide = this.mode === CompareMode.Side;
    this.mode = mode;
    void this.placeAside(this.shown.after, this.generation).then(() => {
      this.arrange();
      if (wasSide || mode === CompareMode.Side) {
        // The element changes its width when the second viewer comes or goes
        this.first.forceResize();
        this.second?.forceResize();
        this.fit(true);
        this.follow(this.first, this.second);
      }
    });
  }

  /** Move the divider of the swipe to a share of the width of the canvas, from 0 to 1. */
  setDivider(share: number): void {
    this.divider = share;
    this.clip();
  }

  /** Show the picture before while a key is held, whatever the mode is. */
  setHolding(holding: boolean): void {
    this.holding = holding;
    this.arrange();
  }

  /** The rectangle of the world that fits the picture, with the room round it. */
  private fitRect(): OpenSeadragon.Rect {
    const size = (this.after ?? this.before)?.getContentSize();
    const aspect = size === undefined ? 0.7 : size.x / size.y;
    const room = this.padding * PAGE_HEIGHT;
    return new OpenSeadragon.Rect(
      -room,
      -room,
      aspect * PAGE_HEIGHT + 2 * room,
      PAGE_HEIGHT + 2 * room,
    );
  }

  /** Fit the picture to the viewport. */
  fit(immediately = false): void {
    const rect = this.fitRect();
    this.first.viewport.fitBounds(rect, immediately);
    this.second?.viewport.fitBounds(rect, immediately);
  }

  /**
   * Tell where the canvas looks, in terms that do not depend on the size of the window.
   *
   * @returns The position, or null while the pictures are being put on the stage.
   */
  readView(): CanvasPositionSchema | null {
    if (!this.settled) {
      return null;
    }
    const { viewport } = this.first;
    const rect = this.fitRect();
    return positionOf(
      viewport.getZoom(),
      viewport.getCenter(),
      fittedWidth(rect, viewport.getAspectRatio()),
    );
  }

  /** Put the canvas at a position that `readView` gave, at once; the layers over it follow the viewport events. */
  private look(position: CanvasPositionSchema): void {
    const { viewport } = this.first;
    const fitted = fittedWidth(this.fitRect(), viewport.getAspectRatio());
    viewport.zoomTo(viewportZoom(position, fitted), undefined, true);
    viewport.panTo(new OpenSeadragon.Point(position.centre_x, position.centre_y), true);
    viewport.applyConstraints(true);
    this.second?.viewport.fitBounds(viewport.getBounds(true), true);
  }

  /** Show the zoom in the document, where the end-to-end scenarios read it. */
  private publishZoom(): void {
    const view = this.readView();
    if (view !== null) {
      this.element.dataset.zoom = String(view.zoom);
    }
  }

  /** Zoom in by one step around the centre of the viewport. */
  zoomIn(): void {
    this.first.viewport.zoomBy(1.5);
    this.first.viewport.applyConstraints();
  }

  /** Zoom out by one step around the centre of the viewport. */
  zoomOut(): void {
    this.first.viewport.zoomBy(1 / 1.5);
    this.first.viewport.applyConstraints();
  }

  /** Release the viewers, their canvases and their listeners. */
  destroy(): void {
    this.generation += 1;
    this.first.destroy();
    this.second?.destroy();
  }

  /** Make the second viewer hold the picture after for the side by side mode, and nothing in the other modes. */
  private async placeAside(after: ImageSource | null, token: number): Promise<void> {
    if (this.mode !== CompareMode.Side || after === null) {
      this.drop(this.second, this.afterAside);
      this.afterAside = null;
      return;
    }
    if (this.second === null) {
      this.second = OpenSeadragon({ element: this.secondElement, ...VIEWER_OPTIONS });
      this.second.addHandler('animation', () => this.follow(this.second, this.first));
    }
    if (this.afterAside === null) {
      this.afterAside = await this.load(this.second, after);
    }
    if (token === this.generation && this.afterAside !== null) {
      this.afterAside.setHeight(PAGE_HEIGHT, true);
      this.afterAside.setPosition(new OpenSeadragon.Point(0, 0), true);
      // The second viewer holds this one picture, which is drawn whenever the side by side mode is on
      this.afterAside.setOpacity(VISIBLE);
    }
  }

  /** Set the opacity and the clip of every picture for the mode and for whether the key is held. */
  private arrange(): void {
    const side = this.mode === CompareMode.Side;
    const swipe = this.mode === CompareMode.Swipe && !this.holding;
    const showBefore = this.holding || side || swipe;
    const showAfter = !this.holding && !side;
    this.before?.setOpacity(showBefore ? VISIBLE : HIDDEN);
    this.after?.setOpacity(showAfter || swipe ? VISIBLE : HIDDEN);
    if (!swipe) {
      this.before?.setClip(null);
    }
    this.clip();
  }

  /** Clip the picture before to the part left of the divider, while a swipe is shown. */
  private clip(): void {
    if (this.mode !== CompareMode.Swipe || this.holding || this.before === null) {
      return;
    }
    const { viewport } = this.first;
    const dividerX = viewport.pointFromPixel(
      new OpenSeadragon.Point(this.divider * viewport.getContainerSize().x, 0),
    ).x;
    const bounds = this.before.getBounds(true);
    const pixels = this.before.getContentSize();
    const width = Math.max(clipWidth(dividerX, bounds.x, bounds.width, pixels.x), LEAST_CLIP_PX);
    this.before.setClip(new OpenSeadragon.Rect(0, 0, width, pixels.y));
  }

  /** Make the other viewer show what this one shows, and the clip of the swipe follow the view. */
  private follow(from: OpenSeadragon.Viewer | null, to: OpenSeadragon.Viewer | null): void {
    this.clip();
    if (from === null || to === null || this.mode !== CompareMode.Side || this.syncing) {
      return;
    }
    // Setting the other viewer fires its own event, which must not set this one back. The two elements are the same size,
    // so the same bounds are the same view, whatever the zoom each viewer counts in its own width
    this.syncing = true;
    to.viewport.fitBounds(from.viewport.getBounds(true), true);
    this.syncing = false;
  }

  private drop(viewer: OpenSeadragon.Viewer | null, item: OpenSeadragon.TiledImage | null): void {
    if (viewer !== null && item !== null && !viewer.isDestroyed()) {
      viewer.world.removeItem(item);
    }
  }

  /** Add a picture to a viewer, hidden until `arrange` shows it. */
  private load(
    viewer: OpenSeadragon.Viewer,
    source: ImageSource,
  ): Promise<OpenSeadragon.TiledImage | null> {
    return new Promise((resolve) => {
      viewer.addTiledImage({
        tileSource: tileSourceOf(source),
        opacity: HIDDEN,
        preload: true,
        height: PAGE_HEIGHT,
        success: (event) => {
          const item = addedItem(event);
          if (item !== null && source.kind === SourceKind.Iiif) {
            nameWholeImageTile(item);
          }
          resolve(item);
        },
        error: () => resolve(null),
      });
    });
  }
}
