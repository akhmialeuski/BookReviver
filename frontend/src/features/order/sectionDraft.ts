import type {
  LabelStyle,
  NumberDisplay,
  PageKind,
  PaginationSectionBody,
  PaginationSectionSchema,
} from '@/api';
import { clampStart, FIRST_NUMBER } from '@/features/order/numbering';

/**
 * The form of a pagination section as plain data, and the body the server takes for it.
 *
 * The server checks the section and writes the numbers, so this module only builds the body: the first page, the name,
 * the style, the first number, the prefix, the display and the kinds the section takes as its own sequence.
 */

/** What the form of a section holds. */
export interface SectionDraft {
  firstId: string;
  name: string;
  style: LabelStyle;
  start: number;
  prefix: string;
  display: NumberDisplay;
  /** The kinds of page the section takes as a sequence of its own, none for a section of the main flow. */
  kinds: readonly PageKind[];
}

/**
 * Start a section at a page.
 *
 * @param firstId The page the section starts at.
 * @returns A section of the main flow that prints Arabic numbers from 1, which is what most sections are.
 */
export function newSectionDraft(firstId: string): SectionDraft {
  return {
    firstId,
    name: '',
    style: 'arabic',
    start: FIRST_NUMBER,
    prefix: '',
    display: 'printed',
    kinds: [],
  };
}

/** Fill the form with a section that exists. */
export function draftOf(section: PaginationSectionSchema): SectionDraft {
  return {
    firstId: section.first_page_id,
    name: section.name,
    style: section.style,
    start: section.start,
    prefix: section.prefix,
    display: section.display,
    kinds: section.kinds,
  };
}

/** Write the form as the body the server takes for a new section and for the replacement of one. */
export function sectionBody(draft: SectionDraft): PaginationSectionBody {
  return {
    first_page_id: draft.firstId,
    name: draft.name.trim(),
    style: draft.style,
    start: clampStart(draft.style, draft.start),
    prefix: draft.prefix,
    display: draft.display,
    kinds: [...draft.kinds],
  };
}
