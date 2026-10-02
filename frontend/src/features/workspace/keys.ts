import type { Stage } from '@/api';
import { STAGES } from '@/features/stages/stages';
import { isEditableTarget, type KeyPress } from '@/features/viewer/keys';

/**
 * The keys of a book screen that are not page turns: the digits that go to a stage, the key of the book description
 * and the question mark that opens the shortcuts. The page turns are the keys of `features/viewer/keys.ts`.
 *
 * A digit is read from the physical key (`code`), since with `Alt` held the character it types depends on the
 * keyboard layout. A key pressed inside a form field or while a dialog is open belongs to that field or dialog.
 */

/** A key press with the physical key, which the digits need. */
export interface BookKeyPress extends KeyPress {
  code: string;
}

/** What a key asks of the book screen. */
export type BookKeyAction =
  | { kind: 'stage'; stage: Stage }
  | { kind: 'about' }
  | { kind: 'shortcuts' };

const DIGIT_CODE = /^Digit([0-9])$/;
const ABOUT_CODE = 'KeyI';

/**
 * The stage a digit goes to: 1 to 9 are the first nine stages in the order of the pipeline, and 0 is the last one.
 *
 * @param digit The digit of the key, 0 to 9.
 * @returns The stage, or null when the book has fewer stages than the digit asks for.
 */
export function stageOfDigit(digit: number): Stage | null {
  const index = digit === 0 ? STAGES.length - 1 : digit - 1;
  return STAGES[index]?.stage ?? null;
}

/**
 * Decide what a key press asks of the book screen.
 *
 * @param press The key press.
 * @param dialogOpen Whether a dialog is open, in which case the keys belong to it.
 * @returns The action, or null when the key is not one of the book screen's.
 */
export function bookKeyAction(press: BookKeyPress, dialogOpen: boolean): BookKeyAction | null {
  if (dialogOpen || press.ctrlKey || press.metaKey || isEditableTarget(press.target)) {
    return null;
  }
  if (press.key === '?' && !press.altKey) {
    return { kind: 'shortcuts' };
  }
  if (!press.altKey || press.shiftKey) {
    return null;
  }
  if (press.code === ABOUT_CODE) {
    return { kind: 'about' };
  }
  const digit = DIGIT_CODE.exec(press.code)?.[1];
  const stage = digit === undefined ? null : stageOfDigit(Number(digit));
  return stage === null ? null : { kind: 'stage', stage };
}
