import type { KonvaEventObject } from 'konva/lib/Node';
import { type RefObject, useEffect, useRef } from 'react';
import { nudgeOfKey } from '@/features/editors/line';
import type { SceneFrame } from '@/features/editors/scene';
import type { Point } from '@/features/editors/shapes';
import { useDebouncedCallback } from '@/shared/hooks/useDebouncedCallback';
import { MESSAGES } from '@/shared/messages';

/**
 * What every editor does with the shape it draws while the reader moves it: keep the latest shape, follow a drag of a
 * handle within the limits of the shape, move it with the arrow keys, and save it when the reader lets go or pauses.
 *
 * The canvases differ in what a handle or a key moves, and say so with a function each; the rest is done once here.
 */

/** Quiet time after the last key before the shape is saved. */
const KEY_SAVE_DELAY_MS = 600;

/** The editing of one shape on the canvas of a scene. */
export interface ShapeEditing<S> {
  /**
   * The shape the handlers build on. They run between renders, so it is the latest one and not the one they closed over.
   */
  latest: RefObject<S>;
  /** Take a shape the reader is still moving as the latest and tell the editor. */
  change: (next: S) => void;
  /** Ask for a save of the shape, which is made once the reader pauses. */
  saveLater: (value: S) => void;
  /** Save the latest shape at once, for a handle the reader let go. */
  release: () => void;
  /**
   * Make the drag handler of a handle.
   *
   * @param move Give the shape with the handle at the place the pointer is, in the pixels of the edit.
   * @param anchor Give the place the handle may stand at in the shape, or undefined when it has none; the handle
   *   follows the pointer only as far as the shape may go.
   * @param grab Called first on every move, for an editor that remembers the handle grabbed last.
   */
  drag: (
    move: (current: S, at: Point) => S,
    anchor: (next: S) => Point | undefined,
    grab?: () => void,
  ) => (event: KonvaEventObject<DragEvent>) => void;
  /**
   * Make the key handler of the layer, which moves the shape by a pixel for an arrow key and by ten with Shift, and saves
   * it after a pause so that a run of presses is saved once.
   *
   * @param nudge Give the shape moved by a step, or null when the key moves nothing.
   */
  onKeyDown: (
    nudge: (current: S, step: Point) => S | null,
  ) => (event: React.KeyboardEvent<HTMLDivElement>) => void;
}

/**
 * Edit one shape on a scene.
 *
 * @param frame The scene at this moment, which converts between the screen and the pixels of the edit.
 * @param shape The shape as the page has it.
 * @param onChange Called while the reader is still moving the shape.
 * @param onCommit Called when the shape is to be saved.
 */
export function useShapeEditing<S>(
  frame: SceneFrame,
  shape: S,
  onChange: (shape: S) => void,
  onCommit: (shape: S) => void,
): ShapeEditing<S> {
  const saveLater = useDebouncedCallback(onCommit, KEY_SAVE_DELAY_MS, {
    // A shape still waiting when the editor goes away is saved at once, so a nudge is never lost to a page turn
    flushOnUnmount: true,
  });
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);
  const { mapping } = frame;

  const change = (next: S): void => {
    latest.current = next;
    onChange(next);
  };

  return {
    latest,
    change,
    saveLater,
    release: () => onCommit(latest.current),
    drag: (move, anchor, grab) => (event) => {
      grab?.();
      const next = move(
        latest.current,
        mapping.toImage({ x: event.target.x(), y: event.target.y() }),
      );
      change(next);
      const placed = anchor(next);
      if (placed !== undefined) {
        event.target.position(mapping.toScreen(placed));
      }
    },
    onKeyDown: (nudge) => (event) => {
      const step = nudgeOfKey(event.key, event.shiftKey);
      if (step === null || event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      const next = nudge(latest.current, step);
      if (next === null) {
        return;
      }
      event.preventDefault();
      change(next);
      saveLater(next);
    },
  };
}

/**
 * Give what an editor sets as the value of its layer, when it is a length in pixels along the width of the image.
 *
 * @param frame The scene, whose width is the largest the length can be.
 * @param pixels The length in pixels of the edit.
 */
export function widthValue(
  frame: SceneFrame,
  pixels: number,
): { min: number; max: number; now: number; text: string } {
  return {
    min: 0,
    max: frame.size.width,
    now: Math.round(pixels),
    text: MESSAGES.processing.thisPage.pixels(pixels),
  };
}
