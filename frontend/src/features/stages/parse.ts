import type { Stage } from '@/api';
import { STAGES } from '@/features/stages/stages';

/**
 * Reading a stage out of an address or a stored value, and choosing the stage a book opens on.
 */

/** The stage a book opens on when nothing says otherwise: the first of the pipeline. */
export const FIRST_STAGE: Stage = 'import';

const STAGE_NAMES: ReadonlySet<string> = new Set(STAGES.map((entry) => entry.stage));

/**
 * Read a stage from text.
 *
 * @param value The text, such as the `$stage` segment of an address.
 * @returns The stage, or null when the text names none.
 */
export function parseStage(value: unknown): Stage | null {
  return typeof value === 'string' && STAGE_NAMES.has(value) ? (value as Stage) : null;
}

/**
 * Choose the stage a book opens on.
 *
 * The stage the reader left the book on wins, then the first stage with work to do that the server names, and a book
 * with neither opens on the first stage.
 *
 * @param remembered The stage the account left the book on, from its place, or null.
 * @param next The `next_stage` of the book, or null when no stage has work to do.
 */
export function startStage(remembered: Stage | null, next: Stage | null): Stage {
  return remembered ?? next ?? FIRST_STAGE;
}
