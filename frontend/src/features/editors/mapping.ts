import OpenSeadragon from 'openseadragon';
import type { Point, Size } from '@/features/editors/shapes';

/**
 * The conversion between the pixels of an image and the pixels of the screen, which keeps an editor on its place on the
 * page while the canvas zooms, pans and turns.
 *
 * OpenSeadragon does the work: a viewport, or one picture of its world, turns a pixel of the image into a pixel of the
 * viewer element and back, whatever the zoom, the pan and the rotation of the view are. This class only adds the step
 * between the pixels an edit is kept in and the pixels the picture is drawn from, which differ when the picture is a
 * reduced copy of the scan.
 */

/** What both an OpenSeadragon viewport and one of its tiled images can do. */
export interface Projection {
  imageToViewerElementCoordinates: (pixel: OpenSeadragon.Point) => OpenSeadragon.Point;
  viewerElementToImageCoordinates: (pixel: OpenSeadragon.Point) => OpenSeadragon.Point;
}

export class SceneMapping {
  private readonly projection: Projection;
  private readonly editSize: Size;
  private readonly pictureSize: Size;

  /**
   * @param projection The viewport, or the one picture the edit lies on.
   * @param editSize The size of the image in the pixels the edit is kept in, such as the scan.
   * @param pictureSize The size of the picture the projection counts in.
   */
  constructor(projection: Projection, editSize: Size, pictureSize: Size) {
    this.projection = projection;
    this.editSize = editSize;
    this.pictureSize = pictureSize;
  }

  /** Give the place on the screen of a point of the edit's image. */
  toScreen(point: Point): Point {
    const picture = new OpenSeadragon.Point(
      (point.x * this.pictureSize.width) / this.editSize.width,
      (point.y * this.pictureSize.height) / this.editSize.height,
    );
    const screen = this.projection.imageToViewerElementCoordinates(picture);
    return { x: screen.x, y: screen.y };
  }

  /** Give the point of the edit's image that lies under a place on the screen. */
  toImage(screen: Point): Point {
    const picture = this.projection.viewerElementToImageCoordinates(
      new OpenSeadragon.Point(screen.x, screen.y),
    );
    return {
      x: (picture.x * this.editSize.width) / this.pictureSize.width,
      y: (picture.y * this.editSize.height) / this.pictureSize.height,
    };
  }

  /** Give the length on the screen of one pixel of the edit's image, along its width. */
  pixelLength(): number {
    const origin = this.toScreen({ x: 0, y: 0 });
    const next = this.toScreen({ x: 1, y: 0 });
    return Math.hypot(next.x - origin.x, next.y - origin.y);
  }
}
