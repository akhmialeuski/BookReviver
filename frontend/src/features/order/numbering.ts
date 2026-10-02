import type { LabelRange, LabelStyle, PageKind, PageSchema } from '@/api';
import { MAX_ROMAN } from '@/features/pages/gaps';

/**
 * The form of the numbering panel as plain data, and what is worked out from it.
 *
 * The server writes the numbers, for the preview as for the save, from one body, so this module only builds the
 * body and reads the range the form names. Which pages get a number and which label, it never decides.
 */

/** What the numbering panel holds: the range, the style, the first number, the brackets and the kinds left out. */
export interface NumberingDraft {
  firstId: string;
  lastId: string;
  style: LabelStyle;
  start: number;
  bracketed: boolean;
  skipKinds: readonly PageKind[];
}

/** The kinds of page a numbering starts with as unnumbered, which a printed book counts but does not number. */
export const DEFAULT_SKIPPED_KINDS: readonly PageKind[] = [
  'cover',
  'back-cover',
  'endpaper',
  'frontispiece',
  'plate',
  'blank',
];

/** The number a numbering starts from unless the form says another. */
export const FIRST_NUMBER = 1;

/**
 * Start a numbering.
 *
 * @param pages The pages of the book in book order.
 * @param firstId The page the range starts at, or the first page of the book when omitted.
 * @returns The draft that numbers from there to the end of the book, or null for a book without pages.
 */
export function newDraft(pages: readonly PageSchema[], firstId?: string): NumberingDraft | null {
  const first = pages.find((page) => page.id === firstId) ?? pages[0];
  const last = pages.at(-1);
  if (first === undefined || last === undefined) {
    return null;
  }
  return {
    firstId: first.id,
    lastId: last.id,
    style: 'arabic',
    start: FIRST_NUMBER,
    bracketed: false,
    skipKinds: DEFAULT_SKIPPED_KINDS,
  };
}

/** Keep the first number a whole number from 1, and within what a Roman numeral can write when the style is one. */
export function clampStart(style: LabelStyle, value: number): number {
  const whole = Number.isFinite(value) ? Math.trunc(value) : FIRST_NUMBER;
  const largest = style === 'arabic' || style === 'none' ? Number.MAX_SAFE_INTEGER : MAX_ROMAN;
  return Math.min(Math.max(whole, FIRST_NUMBER), largest);
}

/** Write the draft as the body the server takes for a numbering and for its preview. */
export function numberingBody(draft: NumberingDraft): LabelRange {
  return {
    first_page_id: draft.firstId,
    last_page_id: draft.lastId,
    style: draft.style,
    start: clampStart(draft.style, draft.start),
    bracketed: draft.bracketed,
    skip_kinds: [...draft.skipKinds],
  };
}

/**
 * Read the pages the draft's range covers.
 *
 * @param pages The pages of the book in book order.
 * @returns The pages from the first to the last one, or null when either is not in the book or the first stands after
 * the last, which the server refuses.
 */
export function pagesInRange(
  pages: readonly PageSchema[],
  draft: NumberingDraft,
): PageSchema[] | null {
  const first = pages.findIndex((page) => page.id === draft.firstId);
  const last = pages.findIndex((page) => page.id === draft.lastId);
  return first < 0 || last < first ? null : pages.slice(first, last + 1);
}

/**
 * Give the page a first run of numbers should end at to stop short of a gap.
 *
 * @param pages The pages of the book in book order.
 * @param beforePageId The page the missing numbers lead to.
 * @returns The page just before it, or null when it is the first page of the book or not in it.
 */
export function endOfRunBefore(
  pages: readonly PageSchema[],
  beforePageId: string,
): PageSchema | null {
  const at = pages.findIndex((page) => page.id === beforePageId);
  return at > 0 ? (pages[at - 1] ?? null) : null;
}
