import type { PageSchema, PaginationSectionSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * What each pagination section of the book covers, read from the section every page names.
 *
 * The server writes the labels and says in `section_id` which section governs each page, so this module holds only what
 * is presentation: the colour of a section, the pages and the range of numbers a row of the panel shows, and the index
 * the grid asks for the section of a tile.
 */

/** The classes that draw a section: the bar at the edge of its row in the panel, and the ring of a number on a tile. */
export interface SectionTone {
  edge: string;
  ring: string;
}

/**
 * The tones counted sections take in turn, which are literal class names so that Tailwind finds them. The order is the
 * order of the sections in the book, so neighbours differ and the boundary between two stands out.
 */
export const SECTION_TONES: readonly SectionTone[] = [
  { edge: 'border-l-purple-500', ring: 'border-purple-500' },
  { edge: 'border-l-blue-500', ring: 'border-blue-500' },
  { edge: 'border-l-green-600', ring: 'border-green-600' },
  { edge: 'border-l-orange-500', ring: 'border-orange-500' },
  { edge: 'border-l-pink-500', ring: 'border-pink-500' },
  { edge: 'border-l-teal-500', ring: 'border-teal-500' },
];

/** The tone of a section whose pages do not count, which is grey as its pages have no number. */
export const NOT_COUNTED_TONE: SectionTone = {
  edge: 'border-l-gray-400',
  ring: 'border-gray-400',
};

/** A section with the pages it numbers and the labels they carry. */
export interface SectionSpan {
  section: PaginationSectionSchema;
  /** The place of the section among the sections, from 1, which names a section that has no name. */
  ordinal: number;
  tone: SectionTone;
  /** The pages the section numbers, in book order. */
  pages: readonly PageSchema[];
  /** The positions, from 1, of the first and the last page of the section, which are the same for a single page. */
  firstPosition: number;
  lastPosition: number;
  /** The first and the last printed number among its pages, or null when none of them shows one. */
  labels: { first: string; last: string } | null;
}

/** Whether a section is a series by kind, which takes the pages of its kinds across the book. */
export function isSeries(section: PaginationSectionSchema): boolean {
  return section.kinds.length > 0;
}

/**
 * Gather what each section covers from the section each page names.
 *
 * The server decides which section governs a page, the same way it numbers the page, and says so in `section_id`, so
 * nothing here works the rule out again. This only groups the pages by that id and reads what a row of the panel shows.
 *
 * @param pages The pages of the book in book order.
 * @param sections The sections of the book in book order, as the server lists them.
 * @returns The sections in the order given, with the pages that name each one.
 */
export function sectionSpans(
  pages: readonly PageSchema[],
  sections: readonly PaginationSectionSchema[],
): SectionSpan[] {
  const taken = new Map<string, PageSchema[]>();
  for (const page of pages) {
    const own = page.section_id === null ? undefined : taken.get(page.section_id);
    if (page.section_id !== null && own === undefined) {
      taken.set(page.section_id, [page]);
    } else {
      own?.push(page);
    }
  }
  const positionOf = new Map(pages.map((page) => [page.id, page.position + 1]));

  let counted = 0;
  return sections.map((section, index): SectionSpan => {
    const own = taken.get(section.id) ?? [];
    const first = positionOf.get(section.first_page_id) ?? 1;
    const positions = [first, ...own.map((page) => page.position + 1)];
    const shown = own.map((page) => page.label).filter((label) => label !== '');
    const tone =
      section.display === 'not-counted'
        ? NOT_COUNTED_TONE
        : (SECTION_TONES[counted++ % SECTION_TONES.length] ?? NOT_COUNTED_TONE);
    return {
      section,
      ordinal: index + 1,
      tone,
      pages: own,
      firstPosition: Math.min(...positions),
      lastPosition: Math.max(...positions),
      labels: shown.length === 0 ? null : { first: shown[0] ?? '', last: shown.at(-1) ?? '' },
    };
  });
}

/** Index the spans by the pages they hold, so a tile can ask which section it is in. */
export function spanOfPages(spans: readonly SectionSpan[]): Map<string, SectionSpan> {
  return new Map(spans.flatMap((span) => span.pages.map((page) => [page.id, span] as const)));
}

/** The name of a section as the reader sees it: its own name, or its place among the sections when it has none. */
export function sectionName(span: SectionSpan): string {
  return span.section.name === ''
    ? MESSAGES.order.sections.unnamed(span.ordinal)
    : span.section.name;
}
