import type { PageHistoryEntrySchema, StepLayer } from '@/api';
import { describeEdit } from '@/features/editors/registry';
import { showValue } from '@/features/processing/pageSettings';

/**
 * Reading the history of a step on a page: which changes still stand, and the text that says what a change did to its
 * layer.
 *
 * The server lists the changes newest first and marks the ones an undo took back, so the changes an undo can take back
 * are those that are no undo and are not marked.
 */

/**
 * The content of a layer as the history keeps it: the fields of the settings, the snapshot of a manual edit, or the
 * processor a change of the type of the step names.
 */
export type LayerContent = Readonly<Record<string, unknown>> | null;

/** The field the content of a change of the type of a step names its processor by. */
const TYPE_FIELD = 'processor_key';

/** Tell whether a change still stands, which is whether an undo can take it back. */
export function stands(change: PageHistoryEntrySchema): boolean {
  return change.source !== 'undo' && change.undone !== true;
}

/**
 * Find the change that Ctrl+Z takes back.
 *
 * @param changes The changes of the step on the page, the newest first.
 * @returns The newest change that stands, or undefined when there is none.
 */
export function lastStanding(
  changes: readonly PageHistoryEntrySchema[],
): PageHistoryEntrySchema | undefined {
  return changes.find(stands);
}

/**
 * Put the content of a layer into words.
 *
 * @param layer The layer the content belongs to.
 * @param content What the layer held, or null for an empty layer.
 * @param titleOf Gives the title a field of the settings goes by.
 * @param typeOf Gives the title a processor goes by, which names the type of the step.
 * @returns The text, or null for an empty layer, which the caller words itself.
 */
export function describeContent(
  layer: StepLayer,
  content: LayerContent,
  titleOf: (name: string) => string,
  typeOf: (key: string) => string = (key) => key,
): string | null {
  if (content === null) {
    return null;
  }
  if (layer === 'type') {
    return typeOf(String(content[TYPE_FIELD]));
  }
  if (layer === 'settings') {
    return Object.entries(content)
      .map(([name, value]) => `${titleOf(name)}: ${showValue(value)}`)
      .join(', ');
  }
  // The snapshot is what the server stored, so a kind that is not text is one no editor has, and is worded as unknown
  return describeEdit(String(content.kind), content.geometry);
}
