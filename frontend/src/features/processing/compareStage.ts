import OpenSeadragon from 'openseadragon';
import type { CanvasPositionSchema } from '@/api';
import type { ViewSize } from '@/features/place/canvas';
import {
  type ComparePair,
  clipWidth,
  type ImageSource,
  innerRect,
  type PairPlacement,
  Side,
  SourceKind,
} from '@/features/processing/compare';
import {
  CanvasStage,
  createViewer,
  HIDDEN,
  type PictureSource,
  SHAPE_DIGITS,
  type StageHooks,
  VISIBLE,
} from '@/features/viewer/canvasStage';
import {
  FALLBACK_ASPECT,
  holdingReach,
  PAGE_HEIGHT,
  type WorldRect,
} from '@/features/viewer/layout';
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
 *
 * Both pictures are as tall as a page and stand at the origin, unless the step that made the picture after moved and scaled
 * its input, as the margins do. Then the one picture stands whole and the other lies inside it where the transform of the
 * step puts it, so the swipe shows the same part of the page on both sides.
 */

/** The least a clip may uncover, since a clip of no width is read as no clip at all. */
const LEAST_CLIP_PX = 0.5;
/** Decimal digits of a place in the world that the scenarios read. */
const PLACE_DIGITS = 4;

/** A rectangle an editor draws that the fit must hold, in the pixels of the picture the editor lies on. */
export interface EditorReach {
  /** The rectangle, which may lie partly or wholly outside the picture. */
  rect: { left: number; top: number; width: number; height: number };
  /** The size of the picture in the pixels of the rectangle, or null when they are the picture's own. */
  size: { width: number; height: number } | null;
}

/** What `show` found out about the pictures it put on the stage. */
export interface ShownCompare {
  /** Which sides could not be read, `before` or `after`. */
  failed: string[];
  /** The addresses of the pictures that were read and are on the stage, the one before first. */
  loaded: string[];
}

function sameSource(a: ImageSource | null, b: ImageSource | null): boolean {
  return a === b || (a !== null && b !== null && a.kind === b.kind && a.url === b.url);
}

function tileSourceOf(source: ImageSource): PictureSource {
  return source.kind === SourceKind.Iiif ? source.url : { type: 'image', url: source.url };
}

export class CompareStage extends CanvasStage {
  private readonly secondElement: HTMLElement;
  private second: OpenSeadragon.Viewer | null = null;
  private before: OpenSeadragon.TiledImage | null = null;
  private after: OpenSeadragon.TiledImage | null = null;
  private afterAside: OpenSeadragon.TiledImage | null = null;
  private shown: ComparePair = { before: null, after: null };
  private placement: PairPlacement | null = null;
  private mode: CompareMode = CompareMode.Off;
  private holding = false;
  private divider = 0.5;
  private syncing = false;
  /** Whether the canvas shows the view `fit` made, which the reader has not moved since. */
  private atFit = false;
  private padding = 0;
  private reach: EditorReach | null = null;

  /**
   * Create the viewers inside two elements.
   *
   * @param element The element the first viewer fills, where the picture before and the swipe are drawn.
   * @param aside The element the second viewer fills, which holds the picture after in the side by side mode.
   */
  constructor(element: HTMLElement, aside: HTMLElement, hooks: StageHooks = {}) {
    super(element, hooks);
    this.secondElement = aside;
    this.viewer.addHandler('animation', () => this.follow(this.viewer, this.second));
    this.viewer.addHandler('resize', () => {
      this.follow(this.viewer, this.second);
      // A canvas that the reader has not moved since it was fitted stays fitted when its element changes size
      if (this.atFit) {
        this.fit(true);
      }
    });
    this.viewer.addHandler('canvas-drag', () => {
      this.atFit = false;
    });
    this.viewer.addHandler('canvas-scroll', () => {
      this.atFit = false;
    });
    this.viewer.addHandler('canvas-pinch', () => {
      this.atFit = false;
    });
    this.viewer.addHandler('canvas-double-click', () => {
      this.atFit = false;
    });
  }

  /** The picture an editor lies on: the one after, else the one before. */
  get image(): OpenSeadragon.TiledImage | null {
    return this.after ?? this.before;
  }

  /**
   * Leave room round the page when it is fitted, such as for the labels an editor puts above it.
   *
   * A view the reader has moved or zoomed is left where it is, and one that is still the fit is made again with the room.
   * The editor of an open step comes onto the page a moment after the pictures, so it must not take back a zoom the reader
   * has set meanwhile.
   *
   * @param share The room on each side as a share of the height of the page.
   */
  setPadding(share: number): void {
    if (share !== this.padding) {
      this.padding = share;
      if (this.atFit) {
        this.fit(true);
      }
    }
  }

  /**
   * Make the fit hold a rectangle an editor draws, beyond the page, so that its handles can be reached.
   *
   * A view the reader has moved is left where it is, and one that is still the fit is made again to hold the rectangle.
   *
   * @param reach The rectangle and the size of the picture it is counted on, or null for the pictures alone.
   */
  setReach(reach: EditorReach | null): void {
    const same = JSON.stringify(reach) === JSON.stringify(this.reach);
    this.reach = reach;
    if (!same && this.atFit) {
      this.fit(true);
    }
  }

  /**
   * Put the two pictures of a page on the stage.
   *
   * @param before The picture before the stage, or null when there is none.
   * @param after The picture after, or null when there is none.
   * @param pageKey What names the pages the pictures belong to. Pictures of the same pages and the same shape as the
   * ones fitted before keep the view of the reader, and any other pictures are fitted.
   * @param placement Where the two pictures stand in one world, or null to draw them as tall as each other.
   * @returns What could not be read, or null when a later call replaced this one before it finished.
   */
  async show(
    before: ImageSource | null,
    after: ImageSource | null,
    pageKey: string,
    placement: PairPlacement | null = null,
  ): Promise<ShownCompare | null> {
    const token = this.begin();
    this.placement = placement;
    if (!sameSource(this.shown.before, before)) {
      this.drop(this.viewer, this.before);
      this.before = null;
    }
    if (!sameSource(this.shown.after, after)) {
      this.drop(this.viewer, this.after);
      this.drop(this.second, this.afterAside);
      this.after = null;
      this.afterAside = null;
    }
    this.shown = { before, after };

    const [loadedAfter, loadedBefore] = await Promise.all([
      this.after ?? (after === null ? null : this.loadPicture(this.viewer, tileSourceOf(after))),
      this.before ?? (before === null ? null : this.loadPicture(this.viewer, tileSourceOf(before))),
    ]);
    if (token !== this.generation) {
      return null;
    }
    this.after = loadedAfter;
    this.before = loadedBefore;
    this.place();
    // The picture before is drawn over the picture after, which a clip then uncovers
    if (this.before !== null) {
      this.viewer.world.setItemIndex(this.before, this.viewer.world.getItemCount() - 1);
    }
    await this.placeAside(after, token);
    this.arrange();
    const world = this.worldRect();
    if (world !== null) {
      this.arrive(
        `${pageKey}|${(world.width / world.height).toFixed(SHAPE_DIGITS)}`,
        (immediately) => this.fit(immediately),
      );
    }
    this.publishPlacement();
    return {
      failed: [
        ...(before !== null && this.before === null ? ['before'] : []),
        ...(after !== null && this.after === null ? ['after'] : []),
      ],
      loaded: [
        ...(before !== null && this.before !== null ? [before.url] : []),
        ...(after !== null && this.after !== null ? [after.url] : []),
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
        this.viewer.forceResize();
        this.second?.forceResize();
        this.fit(true);
        this.follow(this.viewer, this.second);
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

  /** Whether the picture before and the picture after stand by a place, and not each as tall as a page. */
  private placed(): boolean {
    return this.placement !== null && this.before !== null && this.after !== null;
  }

  /** Put the pictures of the first viewer where they stand: as tall as a page at the origin, or one inside the other. */
  private place(): void {
    for (const item of [this.after, this.before]) {
      item?.setHeight(PAGE_HEIGHT, true);
      item?.setPosition(new OpenSeadragon.Point(0, 0), true);
    }
    const { placement, before, after } = this;
    if (placement === null || before === null || after === null) {
      return;
    }
    const [base, inner] = placement.base === Side.Before ? [before, after] : [after, before];
    const rect = innerRect(placement, base.getBounds(true));
    inner.setHeight(rect.height, true);
    inner.setPosition(new OpenSeadragon.Point(rect.x, rect.y), true);
  }

  /** The rectangle of the world that the pictures cover, or null while there is none. */
  private worldRect(): WorldRect | null {
    const { before, after } = this;
    if (this.placed() && before !== null && after !== null) {
      const [first, second] = [before.getBounds(true), after.getBounds(true)];
      const left = Math.min(first.x, second.x);
      const top = Math.min(first.y, second.y);
      return {
        x: left,
        y: top,
        width: Math.max(first.x + first.width, second.x + second.width) - left,
        height: Math.max(first.y + first.height, second.y + second.height) - top,
      };
    }
    const size = (after ?? before)?.getContentSize();
    return size === undefined
      ? null
      : { x: 0, y: 0, width: (size.x / size.y) * PAGE_HEIGHT, height: PAGE_HEIGHT };
  }

  /** Show where the pictures stand in the document, where the end-to-end scenarios read it. */
  private publishPlacement(): void {
    for (const [name, item] of [
      ['beforeBox', this.before],
      ['afterBox', this.after],
    ] as const) {
      const bounds = item?.getBounds(true);
      if (bounds === undefined) {
        delete this.element.dataset[name];
      } else {
        this.element.dataset[name] = [bounds.x, bounds.y, bounds.width, bounds.height]
          .map((value) => value.toFixed(PLACE_DIGITS))
          .join(',');
      }
    }
  }

  /** The rectangle of the world that fits the picture, with the room round it. */
  private fitRect(): WorldRect {
    const world = this.worldRect() ?? {
      x: 0,
      y: 0,
      width: FALLBACK_ASPECT * PAGE_HEIGHT,
      height: PAGE_HEIGHT,
    };
    const held = this.holdingReach(world);
    const room = this.padding * PAGE_HEIGHT;
    return {
      x: held.x - room,
      y: held.y - room,
      width: held.width + 2 * room,
      height: held.height + 2 * room,
    };
  }

  /** Grow the rectangle of the pictures to hold what the editor draws beyond them. */
  private holdingReach(world: WorldRect): WorldRect {
    const picture = this.image;
    const { reach } = this;
    if (picture === null || reach === null) {
      return world;
    }
    const content = picture.getContentSize();
    const bounds = picture.getBounds(true);
    return holdingReach(
      world,
      { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height },
      reach.size ?? { width: content.x, height: content.y },
      { x: reach.rect.left, y: reach.rect.top, width: reach.rect.width, height: reach.rect.height },
    );
  }

  /** Fit the picture to the viewport. */
  fit(immediately = false): void {
    const rect = this.fitRect();
    this.atFit = true;
    for (const viewer of [this.viewer, this.second]) {
      if (viewer !== null) {
        this.fitAboveToolbar(viewer, rect, immediately, this.padding);
      }
    }
  }

  protected override fittedSize(): ViewSize {
    return this.fitRect();
  }

  protected override onMoved(): void {
    this.atFit = false;
  }

  /** Put the canvas at a position that `readView` gave, at once; the second viewer follows. */
  protected override look(position: CanvasPositionSchema): void {
    super.look(position);
    this.second?.viewport.fitBounds(this.viewer.viewport.getBounds(true), true);
  }

  /** Release the viewers, their canvases and their listeners. */
  override destroy(): void {
    super.destroy();
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
      this.second = createViewer(this.secondElement);
      this.second.addHandler('animation', () => this.follow(this.second, this.viewer));
    }
    if (this.afterAside === null) {
      this.afterAside = await this.loadPicture(this.second, tileSourceOf(after));
    }
    if (token === this.generation && this.afterAside !== null) {
      // The picture after stands in the second viewer where it stands in the first, so the two views share one world
      const bounds = this.placed() ? this.after?.getBounds(true) : undefined;
      this.afterAside.setHeight(bounds?.height ?? PAGE_HEIGHT, true);
      this.afterAside.setPosition(new OpenSeadragon.Point(bounds?.x ?? 0, bounds?.y ?? 0), true);
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
    const { viewport } = this.viewer;
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
}
