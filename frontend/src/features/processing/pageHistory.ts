import type { PageStepChangeSchema, StepLayer } from '@/api';
import {
  type Geometry,
  readLine,
  readMesh,
  readQuad,
  readRect,
  readRegions,
  readRotation,
  readSplit,
} from '@/features/editors/shapes';
import { showValue } from '@/features/processing/pageSettings';
import { MESSAGES } from '@/shared/messages';

/**
 * Reading the history of a step on a page: which changes still stand, and the text that says what a change did to its
 * layer.
 *
 * The server lists the changes newest first and marks the ones an undo took back, so the changes an undo can take back
 * are those that are no undo and are not marked.
 */

/** The content of a layer as the history keeps it: the fields of the settings, or the snapshot of a manual edit. */
export type LayerContent = Readonly<Record<string, unknown>> | null;

/** How the content of a layer is put into words. */
export interface ContentWords {
  /** The title a field of the settings goes by. */
  titleOf: (name: string) => string;
}

/** Tell whether a change still stands, which is whether an undo can take it back. */
export function stands(change: PageStepChangeSchema): boolean {
  return change.source !== 'undo' && change.undone !== true;
}

/**
 * Find the change that Ctrl+Z takes back.
 *
 * @param changes The changes of the step on the page, the newest first.
 * @returns The newest change that stands, or undefined when there is none.
 */
export function lastStanding(
  changes: readonly PageStepChangeSchema[],
): PageStepChangeSchema | undefined {
  return changes.find(stands);
}

/**
 * Put a manual edit into words, by the kind of editor that made it.
 *
 * The snapshot is what the server writes for the hand layer, the kind of the editor and the shape it drew. Pixels of the
 * page are rounded to whole numbers, and an angle to one decimal.
 *
 * @param snapshot The content of the hand layer.
 * @returns The text, which is "Set by hand" for a kind that is not known or a shape that does not fit its kind.
 */
function describeEdit(snapshot: Readonly<Record<string, unknown>>): string {
  const words = MESSAGES.processing.steps.pageHistory.hand;
  // The readers take the shape as the API serves it, a map of numbers, and return null for anything else
  const stored = snapshot.geometry;
  const shape = typeof stored === 'object' && stored !== null ? (stored as Geometry) : null;
  switch (snapshot.kind) {
    case 'rect':
    case 'content-box': {
      const frame = readRect(shape);
      return frame === null
        ? words.unknown
        : words.frame(
            Math.round(frame.left),
            Math.round(frame.top),
            Math.round(frame.width),
            Math.round(frame.height),
          );
    }
    case 'line':
    case 'split': {
      const line = snapshot.kind === 'split' ? readSplit(shape)?.line : readLine(shape);
      return line === null || line === undefined
        ? words.unknown
        : words.line(
            Math.round(line.start.x),
            Math.round(line.start.y),
            Math.round(line.end.x),
            Math.round(line.end.y),
          );
    }
    case 'rotation': {
      const rotation = readRotation(shape);
      return rotation === null
        ? words.unknown
        : words.angle(MESSAGES.processing.thisPage.degrees(rotation.degrees));
    }
    case 'quad':
      return readQuad(shape) === null ? words.unknown : words.quad;
    case 'mesh':
      return readMesh(shape) === null ? words.unknown : words.mesh;
    case 'brush-mask':
      return words.mask;
    case 'regions': {
      const regions = readRegions(shape);
      return regions === null ? words.unknown : words.regions(regions.zones.length);
    }
    default:
      return words.unknown;
  }
}

/**
 * Put the content of a layer into words.
 *
 * @param layer The layer the content belongs to.
 * @param content What the layer held, or null for an empty layer.
 * @param words The titles the text is made of.
 * @returns The text, or null for an empty layer, which the caller words itself.
 */
export function describeContent(
  layer: StepLayer,
  content: LayerContent,
  words: ContentWords,
): string | null {
  if (content === null) {
    return null;
  }
  if (layer === 'settings') {
    return Object.entries(content)
      .map(([name, value]) => `${words.titleOf(name)}: ${showValue(value)}`)
      .join(', ');
  }
  return describeEdit(content);
}
