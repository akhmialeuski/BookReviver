import { useSyncExternalStore } from 'react';
import type { BrushShape, Point, Size, StrokeShape } from '@/features/editors/shapes';
import { MESSAGES } from '@/shared/messages';

/**
 * The arithmetic of the brush editor of the eraser: how thick the brush is, a stroke that grows as the pointer moves, and
 * the mask that the strokes are painted into for the server.
 *
 * The size of the brush belongs to the reader and not to a page, so it is kept for the whole session in a store of its own
 * that the canvas and the panel both read. It is a share of the width of the page, so a stroke covers the same part of a
 * page whatever the resolution of the scan.
 */

/** The thinnest and the thickest brush, as a percent of the width of the page, and the step between them. */
export const BRUSH_MIN_PERCENT = 0.5;
export const BRUSH_MAX_PERCENT = 10;
export const BRUSH_STEP_PERCENT = 0.5;
const BRUSH_DEFAULT_PERCENT = 2;
const PERCENT = 100;
const DIAMETER = 2;

/** What the mask is painted with where the reader brushed, and where not. */
const BRUSHED_COLOR = '#ffffff';
const UNTOUCHED_COLOR = '#000000';
const MASK_TYPE = 'image/png';

let percent = BRUSH_DEFAULT_PERCENT;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Read the size of the brush, as a percent of the width of the page, and change it. */
export function useBrushPercent(): [number, (next: number) => void] {
  const current = useSyncExternalStore(
    subscribe,
    () => percent,
    () => BRUSH_DEFAULT_PERCENT,
  );
  const set = (next: number): void => {
    percent = Math.min(Math.max(next, BRUSH_MIN_PERCENT), BRUSH_MAX_PERCENT);
    for (const listener of listeners) {
      listener();
    }
  };
  return [current, set];
}

/** Give the radius in image pixels of a brush of a size, as a percent of the width of the image. */
export function radiusOf(brushPercent: number, size: Size): number {
  return (size.width * brushPercent) / PERCENT / DIAMETER;
}

/** Begin a stroke at a point. */
export function startStroke(at: Point, radius: number): StrokeShape {
  return { radius, points: [at] };
}

/** Add a point to the stroke that is being made, which is the last one of the brush. */
export function extendStroke(brush: BrushShape, at: Point): BrushShape {
  const last = brush.strokes.at(-1);
  if (last === undefined) {
    return brush;
  }
  return {
    strokes: [...brush.strokes.slice(0, -1), { ...last, points: [...last.points, at] }],
  };
}

/**
 * Paint the strokes into a mask: white where the reader brushed and black elsewhere, the size of the page the strokes were
 * brushed on.
 *
 * @param brush The strokes.
 * @param size The size of the image in the pixels the strokes are in.
 * @returns The mask as a PNG.
 */
export async function paintMask(brush: BrushShape, size: Size): Promise<Blob> {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(size.width));
  canvas.height = Math.max(1, Math.round(size.height));
  const context = canvas.getContext('2d');
  if (context === null) {
    throw new Error(MESSAGES.editors.brush.noCanvas);
  }
  context.fillStyle = UNTOUCHED_COLOR;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = BRUSHED_COLOR;
  context.fillStyle = BRUSHED_COLOR;
  context.lineCap = 'round';
  context.lineJoin = 'round';
  for (const stroke of brush.strokes) {
    context.lineWidth = stroke.radius * DIAMETER;
    const [first, ...rest] = stroke.points;
    if (first === undefined) {
      continue;
    }
    context.beginPath();
    // A click paints a disc, which a path of one point does not
    context.arc(first.x, first.y, stroke.radius, 0, Math.PI * DIAMETER);
    context.fill();
    context.beginPath();
    context.moveTo(first.x, first.y);
    for (const point of rest) {
      context.lineTo(point.x, point.y);
    }
    context.stroke();
  }
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob === null) {
        reject(new Error(MESSAGES.editors.brush.noPicture));
      } else {
        resolve(blob);
      }
    }, MASK_TYPE);
  });
}
