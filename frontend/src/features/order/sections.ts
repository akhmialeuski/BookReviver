import type { PageKind, PageSchema, PaginationSectionSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * Which pagination section each page of the book belongs to, worked out the way the server numbers the pages.
 *
 * The server writes the labels, and this module only reads the same rule over the same sections, so the panel can list
 * the sections with the pages and numbers they cover and the grid can colour each page by its section. A section of the
 * main flow lasts until the next one of the main flow starts, and a series by kind takes the pages of its kinds from its
 * first page on, ahead of the main flow.
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
 * Read which section governs each page and what each section covers.
 *
 * @param pages The pages of the book in book order.
 * @param sections The sections of the book, in any order. A section whose first page is not in the book is left out,
 * since it starts nowhere.
 * @returns The sections in book order, which is the order of the pages they start at, with the main flow ahead of a
 * series on the same page.
 */
export function sectionSpans(
  pages: readonly PageSchema[],
  sections: readonly PaginationSectionSchema[],
): SectionSpan[] {
  const indexOf = new Map(pages.map((page, index) => [page.id, index]));
  const heads = sections
    .flatMap((section) => {
      const at = indexOf.get(section.first_page_id);
      return at === undefined ? [] : [{ section, at }];
    })
    .toSorted(
      (a, b) =>
        a.at - b.at ||
        Number(isSeries(a.section)) - Number(isSeries(b.section)) ||
        a.section.created_at.localeCompare(b.section.created_at) ||
        a.section.id.localeCompare(b.section.id),
    );

  // The pages are dealt out in one pass, a section taking effect at the page it starts at
  const taken = new Map<string, PageSchema[]>(heads.map(({ section }) => [section.id, []]));
  let flow: PaginationSectionSchema | undefined;
  const series = new Map<PageKind, PaginationSectionSchema>();
  for (const [index, page] of pages.entries()) {
    for (const { section } of heads.filter(({ at }) => at === index)) {
      if (isSeries(section)) {
        for (const kind of section.kinds) {
          series.set(kind, section);
        }
      } else {
        flow = section;
      }
    }
    const governing = series.get(page.kind) ?? flow;
    if (governing !== undefined) {
      taken.get(governing.id)?.push(page);
    }
  }

  let counted = 0;
  return heads.map(({ section, at }, index): SectionSpan => {
    const own = taken.get(section.id) ?? [];
    const positions = [at + 1, ...own.map((page) => page.position + 1)];
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
