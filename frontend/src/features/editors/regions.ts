import { clampToScan } from '@/features/editors/line';
import type { Point, RegionsShape, Size, ZoneMode, ZoneShape } from '@/features/editors/shapes';

/**
 * The arithmetic of the picture zone editor: a new zone, a corner moved, a zone taken away, and the box a zone fills.
 *
 * Everything is in the pixels of the image the step reads. A zone the reader adds is a rectangle of four corners that
 * stands in the middle of the page, and each corner moves on its own, so the zone can become any four-sided shape.
 */

/** The share of each side of the page that a new zone covers. */
const NEW_ZONE_SHARE = 0.4;

const HALF = 2;

/**
 * Give a new zone: a rectangle in the middle of the page.
 *
 * @param mode Whether the zone adds a picture or removes one.
 * @param size The size of the image.
 */
export function newZone(mode: ZoneMode, size: Size): ZoneShape {
  const left = (size.width * (1 - NEW_ZONE_SHARE)) / HALF;
  const top = (size.height * (1 - NEW_ZONE_SHARE)) / HALF;
  const right = left + size.width * NEW_ZONE_SHARE;
  const bottom = top + size.height * NEW_ZONE_SHARE;
  return {
    mode,
    points: [
      { x: left, y: top },
      { x: right, y: top },
      { x: right, y: bottom },
      { x: left, y: bottom },
    ],
  };
}

/** Add a zone after the others. */
export function addZone(regions: RegionsShape, zone: ZoneShape): RegionsShape {
  return { zones: [...regions.zones, zone] };
}

/** Take a zone away. */
export function removeZone(regions: RegionsShape, index: number): RegionsShape {
  return { zones: regions.zones.filter((_, place) => place !== index) };
}

/**
 * Move a corner of a zone to a point, held on the image.
 *
 * @param regions The zones.
 * @param zone The place of the zone in the list.
 * @param corner The place of the corner in the zone.
 * @param to Where the pointer is, in the pixels of the image.
 * @param size The size of the image.
 */
export function moveCorner(
  regions: RegionsShape,
  zone: number,
  corner: number,
  to: Point,
  size: Size,
): RegionsShape {
  const point = clampToScan(to, size);
  return {
    zones: regions.zones.map((entry, place) =>
      place === zone
        ? { ...entry, points: entry.points.map((old, at) => (at === corner ? point : old)) }
        : entry,
    ),
  };
}

/** Give the box a zone fills, as left, top, width and height. */
export function boundsOf(points: readonly Point[]): [number, number, number, number] {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  return [left, top, Math.max(...xs) - left, Math.max(...ys) - top];
}
