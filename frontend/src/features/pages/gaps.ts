import type { LabelStyle } from '@/api';

/**
 * Finding the pages a book lacks from the printed numbers of the pages it has.
 *
 * The labels are read in book order as numbers, Arabic and Roman, with or without the square brackets that mark a
 * number the page does not print. A jump from one number to a much larger one in the same style is a gap, and the
 * places for the missing numbers are told. Pages without a number, such as plates and blank leaves, stand in for the
 * numbers a printed book gives them, so a jump they can explain is not a gap. A page left out of the book is not
 * part of it and is read as if it were not there.
 */

/** The styles a number is read in, which are the numbers a printed book counts its pages with. */
export type NumberStyle = Extract<LabelStyle, 'arabic' | 'roman-lower' | 'roman-upper'>;

/** What a label says: a number and the style it is written in. */
export interface ParsedLabel {
  style: NumberStyle;
  value: number;
}

/** The part of a page the gaps are read from, which a page of the manifest has and a page with a new label can have. */
export interface LabelledPage {
  id: string;
  label: string;
  included: boolean;
}

/** A jump in the printed numbers, with the numbers that are missing and the pages on both sides of them. */
export interface LabelGap {
  /** The last numbered page before the jump. */
  afterPageId: string;
  /** The first numbered page after the jump, which the missing pages stand before. */
  beforePageId: string;
  style: NumberStyle;
  /** The number the jump starts from and the number it lands on. */
  jumpFrom: number;
  jumpTo: number;
  /** The first and last of the numbers that have no page. */
  firstMissing: number;
  lastMissing: number;
}

const ARABIC = /^\d+$/;
const BRACKETED = /^\[(.+)\]$/;

const ROMAN_DIGITS: readonly (readonly [string, number])[] = [
  ['m', 1000],
  ['cm', 900],
  ['d', 500],
  ['cd', 400],
  ['c', 100],
  ['xc', 90],
  ['l', 50],
  ['xl', 40],
  ['x', 10],
  ['ix', 9],
  ['v', 5],
  ['iv', 4],
  ['i', 1],
];
const ROMAN_VALUE: Readonly<Record<string, number>> = {
  i: 1,
  v: 5,
  x: 10,
  l: 50,
  c: 100,
  d: 500,
  m: 1000,
};

/** The largest number a Roman numeral writes, which the numbering of the server stops at as well. */
export const MAX_ROMAN = 3999;

/** Write a number as a lower-case Roman numeral. */
function toRoman(value: number): string {
  let rest = value;
  let text = '';
  for (const [digits, worth] of ROMAN_DIGITS) {
    while (rest >= worth) {
      text += digits;
      rest -= worth;
    }
  }
  return text;
}

/** Read a lower-case Roman numeral, or null when the text is not written the way {@link toRoman} writes it. */
function fromRoman(text: string): number | null {
  let value = 0;
  for (let at = 0; at < text.length; at += 1) {
    const here = ROMAN_VALUE[text.charAt(at)];
    const next = ROMAN_VALUE[text.charAt(at + 1)];
    if (here === undefined) {
      return null;
    }
    value += next !== undefined && next > here ? -here : here;
  }
  // Only the canonical spelling is a number: iiii, vx and the like are not
  return value > 0 && toRoman(value) === text ? value : null;
}

/**
 * Read the number a label stands for.
 *
 * @param label The printed number of a page, such as `47`, `xii`, `IV` or `[1]`.
 * @returns The number and its style, or null for an empty label and for text that is no number.
 */
export function parseLabel(label: string): ParsedLabel | null {
  const text = (BRACKETED.exec(label.trim())?.[1] ?? label).trim();
  if (ARABIC.test(text)) {
    const value = Number.parseInt(text, 10);
    return value > 0 ? { style: 'arabic', value } : null;
  }
  const lower = text.toLowerCase();
  const style = text === lower ? 'roman-lower' : text === text.toUpperCase() ? 'roman-upper' : null;
  const value = style === null ? null : fromRoman(lower);
  return style === null || value === null ? null : { style, value };
}

/**
 * Write a number in a style, the way the server writes it into a label.
 *
 * @param style How the number is written.
 * @param value The number, from 1.
 */
export function formatNumber(style: NumberStyle, value: number): string {
  if (style === 'arabic') {
    return String(value);
  }
  const roman = toRoman(value);
  return style === 'roman-lower' ? roman : roman.toUpperCase();
}

/** The numbered page the next numbered page is compared with. */
interface Run {
  pageId: string;
  parsed: ParsedLabel;
}

/**
 * Find the jumps in the printed numbers of a book.
 *
 * Two numbered pages next to each other, with only unnumbered pages between them, make a gap when the second number
 * is more than one above the first, both are in the same style, and the unnumbered pages between them are too few
 * to hold the numbers in between. A page without a number holds one number, as a plate or a blank leaf does in a
 * printed book, and the missing numbers that remain are the last ones of the jump. A change of style, a number that
 * does not rise, and text that is no number make no gap, and a page that is left out of the book is skipped.
 *
 * @param pages The pages of the book in book order.
 * @returns The gaps in book order.
 */
export function findGaps(pages: readonly LabelledPage[]): LabelGap[] {
  const gaps: LabelGap[] = [];
  let previous: Run | null = null;
  let unnumbered = 0;
  for (const page of pages) {
    if (!page.included) {
      continue;
    }
    const parsed = parseLabel(page.label);
    if (parsed === null) {
      unnumbered += 1;
      continue;
    }
    if (previous !== null && previous.parsed.style === parsed.style) {
      const missing = parsed.value - previous.parsed.value - 1;
      if (missing > unnumbered) {
        gaps.push({
          afterPageId: previous.pageId,
          beforePageId: page.id,
          style: parsed.style,
          jumpFrom: previous.parsed.value,
          jumpTo: parsed.value,
          firstMissing: previous.parsed.value + 1 + unnumbered,
          lastMissing: parsed.value - 1,
        });
      }
    }
    previous = { pageId: page.id, parsed };
    unnumbered = 0;
  }
  return gaps;
}

/** How many pages a gap lacks. */
export function missingCount(gap: LabelGap): number {
  return gap.lastMissing - gap.firstMissing + 1;
}

/**
 * Pick the gaps a new set of labels closes.
 *
 * @param current The gaps of the labels the pages have now.
 * @param proposed The gaps of the labels a numbering would write.
 * @returns The current gaps whose two sides are not a gap any more, which the numbering would hide.
 */
export function hiddenGaps(
  current: readonly LabelGap[],
  proposed: readonly LabelGap[],
): LabelGap[] {
  return current.filter(
    (gap) =>
      !proposed.some(
        (other) => other.afterPageId === gap.afterPageId && other.beforePageId === gap.beforePageId,
      ),
  );
}

/**
 * Put the labels of a numbering on the pages that get one, leaving the others as they are.
 *
 * @param pages The pages of the book in book order.
 * @param labels The new label of each page the numbering counts, by page id.
 */
export function withLabels<T extends LabelledPage>(
  pages: readonly T[],
  labels: ReadonlyMap<string, string>,
): T[] {
  return pages.map((page) => {
    const label = labels.get(page.id);
    return label === undefined ? page : { ...page, label };
  });
}
