import OpenSeadragon from 'openseadragon';
import type { CanvasPositionSchema } from '@/api';
import { fittedWidth, positionOf, type ViewSize, viewportZoom } from '@/features/place/canvas';
import {
  PAGE_HEIGHT,
  TOOLBAR_INSET_PX,
  type WorldRect,
  withBottomInset,
} from '@/features/viewer/layout';

/**
 * The part of an OpenSeadragon canvas that the reader of the book and the before-and-after compare share: the viewer
 * and its settings, the pictures added hidden, the fit above the toolbar, the position that is read and restored, the
 * zoom steps, and the rule for the first view of a screen.
 *
 * The subclasses keep what is their own: which pictures stand on the canvas, where they stand, and which rectangle of
 * the world a fit shows. A rule of the view, such as the saved zoom that an empty view must not use up, lives here once.
 */

/** What the screen lets a stage ask for and tell, so the place of the reader can be kept and restored. */
export interface StageHooks {
  /** Asked once, when the first view is placed: where the canvas looked when the book was left, or null for fitted. */
  restore?: () => CanvasPositionSchema | null;
  /** Called when the canvas has stopped moving. */
  onViewChange?: () => void;
}

/** A tile source of OpenSeadragon: the address of an IIIF `info.json`, or a plain image. */
export type PictureSource = string | { type: string; url: string };

const ZOOM_STEP = 1.5;
const ANIMATION_SECONDS = 0.35;
export const HIDDEN = 0;
export const VISIBLE = 1;
/** Decimal digits of the width of a view that tell one shape from another. */
export const SHAPE_DIGITS = 2;

// OpenSeadragon asks for the tile that is a whole image at full size as `full/max/`, which an IIIF 3 server
// resolves, while the pyramid is stored as files that `dzsave` named `full/<width>,<height>/`
const WHOLE_IMAGE_SIZE = '/full/max/';

/** The settings both kinds of viewer share. */
const VIEWER_OPTIONS = {
  // The screen has its own buttons and its own keys, so a page turn is not an arrow key panning the image
  showNavigationControl: false,
  showNavigator: false,
  keyboardNavEnabled: false,
  // The stage fits and restores the view itself, and a world that is down to one picture must not send it home
  preserveViewport: true,
  animationTime: ANIMATION_SECONDS,
  visibilityRatio: 0.5,
  minZoomImageRatio: 0.5,
  maxZoomPixelRatio: 3,
  gestureSettingsMouse: { scrollToZoom: true, clickToZoom: false, dblClickToZoom: true },
  gestureSettingsTouch: { pinchToZoom: true, clickToZoom: false, dblClickToZoom: true },
};

/** Create a viewer inside an element, with the settings of the stages. */
export function createViewer(element: HTMLElement): OpenSeadragon.Viewer {
  return OpenSeadragon({ element, ...VIEWER_OPTIONS });
}

/** The rectangle of the world as the object OpenSeadragon takes. */
export function toRect(rect: WorldRect): OpenSeadragon.Rect {
  return new OpenSeadragon.Rect(rect.x, rect.y, rect.width, rect.height);
}

/** Pull the added image out of the object OpenSeadragon passes to the `success` callback of `addTiledImage`. */
function addedItem(event: unknown): OpenSeadragon.TiledImage | null {
  if (typeof event === 'object' && event !== null && 'item' in event) {
    return event.item as OpenSeadragon.TiledImage;
  }
  return null;
}

/**
 * Make a pyramid whose single tile is the whole image findable.
 *
 * The tile route serves the files as they are stored and resolves no IIIF size keywords, so a page of at most one
 * tile in each direction would otherwise stay blank. The size keyword is rewritten to the stored one, and every
 * other tile address is left as OpenSeadragon builds it.
 */
export function nameWholeImageTile(item: OpenSeadragon.TiledImage): void {
  const { source } = item;
  const original = source.getTileUrl.bind(source);
  const stored = `/full/${source.dimensions.x},${source.dimensions.y}/`;
  source.getTileUrl = (level, x, y) => {
    const url = original(level, x, y);
    // OpenSeadragon allows a function that makes the address later, which an IIIF source never returns
    return typeof url === 'string' ? url.replace(WHOLE_IMAGE_SIZE, stored) : url;
  };
}

/** The latest picture asked of each viewer, which the next one waits for. It never rejects: a failure is a null. */
const adding = new WeakMap<OpenSeadragon.Viewer, Promise<unknown>>();

export abstract class CanvasStage {
  /** The viewer the stage fills; a layer drawn over the canvas follows it. */
  readonly viewer: OpenSeadragon.Viewer;
  protected readonly element: HTMLElement;
  protected readonly hooks: StageHooks;
  /** Counts the calls that put pictures on the stage, so an older call can see that a newer one replaced it. */
  protected generation = 0;
  private hasFitted = false;
  /** What the view that was last fitted whole was made of, or null while none was. */
  private fittedFor: string | null = null;
  /** Whether the latest view is on the stage, so the position of the canvas belongs to it. */
  private settled = false;

  /**
   * Create the viewer inside an element.
   *
   * @param element The element OpenSeadragon fills; it needs a size of its own.
   * @param hooks What the screen asks of the stage and is told by it about the position of the canvas.
   */
  constructor(element: HTMLElement, hooks: StageHooks = {}) {
    this.element = element;
    this.hooks = hooks;
    this.viewer = createViewer(element);
    this.viewer.addHandler('animation-finish', () => {
      this.publishZoom();
      this.hooks.onViewChange?.();
    });
  }

  /** The size of the view that a fit shows, in the units of the world, or null while there is nothing to fit. */
  protected abstract fittedSize(): ViewSize | null;

  /** Called when the reader's view moves away from the fit: a restored place or a zoom step. */
  protected onMoved(): void {}

  /**
   * Start putting a view on the stage.
   *
   * @returns The number of this call, which is still `generation` while no later call has replaced it.
   */
  protected begin(): number {
    this.settled = false;
    return ++this.generation;
  }

  /**
   * Place the view that has just been put on the stage, if it has a picture to draw.
   *
   * The first view of a screen goes back to where the reader left the canvas, and every later one is fitted, unless it
   * is made of the same thing as the one fitted before. A view with nothing to draw, such as pages whose rows are still
   * being read, is not a view of the reader: the call is left out, and the place to be restored, the fit and the zoom
   * of the view before stay as they are, for the first picture to take up.
   *
   * @param key What the view is made of; the same key as the last fitted view keeps the zoom of the reader.
   * @param refit Fits the view; it is told whether to do it at once.
   * @param complete Whether every picture of the view was read. A view with a picture missing is fitted again the next
   * time it is shown.
   */
  protected arrive(key: string, refit: (immediately: boolean) => void, complete = true): void {
    const restored = this.hasFitted ? null : (this.hooks.restore?.() ?? null);
    if (restored !== null) {
      this.look(restored);
    } else if (key !== this.fittedFor) {
      refit(!this.hasFitted);
    }
    this.fittedFor = complete ? key : null;
    this.hasFitted = true;
    this.settled = true;
  }

  /**
   * Tell where the canvas looks, in terms that do not depend on the size of the window.
   *
   * @returns The position, or null while a view is still being put on the stage, so a position never pairs the
   * zoom of the view that is leaving with the one that is arriving.
   */
  readView(): CanvasPositionSchema | null {
    const fitted = this.settled ? this.fittedSize() : null;
    if (fitted === null) {
      return null;
    }
    const { viewport } = this.viewer;
    return positionOf(
      viewport.getZoom(),
      viewport.getCenter(),
      fittedWidth(fitted, viewport.getAspectRatio()),
    );
  }

  /** Put the canvas at a position that `readView` gave, at once; the layers over it follow the viewport events. */
  protected look(position: CanvasPositionSchema): void {
    const fitted = this.fittedSize();
    if (fitted === null) {
      return;
    }
    const { viewport } = this.viewer;
    this.onMoved();
    viewport.zoomTo(
      viewportZoom(position, fittedWidth(fitted, viewport.getAspectRatio())),
      undefined,
      true,
    );
    viewport.panTo(new OpenSeadragon.Point(position.centre_x, position.centre_y), true);
    viewport.applyConstraints(true);
  }

  /**
   * Fit a rectangle of the world to the part of a viewer that the toolbar does not cover.
   *
   * The toolbar floats over the bottom of the canvas. The room an editor asks round the page already keeps the page off
   * the bottom edge, so only what the toolbar covers beyond it is added, which keeps the zoom of the editors as near as
   * it can to what it was.
   *
   * @param viewer The viewer to fit.
   * @param rect The rectangle to show, with the room round it already added.
   * @param immediately Whether to jump instead of animating.
   * @param roomShare The room round the page that `rect` holds, as a share of the height of the page.
   */
  protected fitAboveToolbar(
    viewer: OpenSeadragon.Viewer,
    rect: WorldRect,
    immediately: boolean,
    roomShare = 0,
  ): void {
    const container = viewer.viewport.getContainerSize();
    const size = { width: container.x, height: container.y };
    const roomPx =
      Math.min(size.width / rect.width, size.height / rect.height) * roomShare * PAGE_HEIGHT;
    const inset = withBottomInset(rect, size, Math.max(0, TOOLBAR_INSET_PX - roomPx));
    viewer.viewport.fitBounds(toRect(inset), immediately);
  }

  /** Zoom in by one step around the centre of the viewport. */
  zoomIn(): void {
    this.zoomBy(ZOOM_STEP);
  }

  /** Zoom out by one step around the centre of the viewport. */
  zoomOut(): void {
    this.zoomBy(1 / ZOOM_STEP);
  }

  /** Release the viewer, its canvases and its listeners. */
  destroy(): void {
    this.generation += 1;
    this.viewer.destroy();
  }

  /**
   * Add a picture to a viewer, hidden until the stage shows it.
   *
   * The pictures of one viewer are added one after another. OpenSeadragon keeps the pictures it is adding in a queue
   * and hands them over in the order they were asked for, but a picture that fails leaves the queue without handing
   * over the ones behind it that are already read, so their `success` is never called. With two pictures in flight, a
   * 404 on the first while the second was read before it left the second promise pending for good, and the canvas
   * stayed in the loading state. One picture in flight at a time cannot be overtaken.
   *
   * @param viewer The viewer to add the picture to.
   * @param tileSource The address of an IIIF `info.json`, or a plain image.
   * @returns The picture, or null when it could not be read or the viewer is gone.
   */
  protected loadPicture(
    viewer: OpenSeadragon.Viewer,
    tileSource: PictureSource,
  ): Promise<OpenSeadragon.TiledImage | null> {
    const added = (adding.get(viewer) ?? Promise.resolve()).then(
      () =>
        new Promise<OpenSeadragon.TiledImage | null>((resolve) => {
          if (viewer.isDestroyed()) {
            resolve(null);
            return;
          }
          viewer.addTiledImage({
            tileSource,
            opacity: HIDDEN,
            preload: true,
            height: PAGE_HEIGHT,
            success: (event) => {
              const item = addedItem(event);
              if (item !== null && typeof tileSource === 'string') {
                // Before the next frame, which is the first that asks for a tile
                nameWholeImageTile(item);
              }
              resolve(item);
            },
            error: () => resolve(null),
          });
        }),
    );
    adding.set(viewer, added);
    return added;
  }

  /** Take a picture out of the world of a viewer, unless the viewer is gone. */
  protected drop(viewer: OpenSeadragon.Viewer | null, item: OpenSeadragon.TiledImage | null): void {
    if (viewer !== null && item !== null && !viewer.isDestroyed()) {
      viewer.world.removeItem(item);
    }
  }

  private zoomBy(factor: number): void {
    this.onMoved();
    this.viewer.viewport.zoomBy(factor);
    this.viewer.viewport.applyConstraints();
  }

  /** Show the zoom in the document, where the end-to-end scenarios read it. */
  private publishZoom(): void {
    const view = this.readView();
    if (view !== null) {
      this.element.dataset.zoom = String(view.zoom);
    }
  }
}
