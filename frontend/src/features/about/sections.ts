/**
 * The sections of the About tab, in the order the list on the left and the form show them.
 */

export const Section = {
  Title: 'title',
  Publication: 'publication',
  Copy: 'copy',
  Subjects: 'subjects',
  Notes: 'notes',
  Storage: 'storage',
} as const;

/** One section (derived from {@link Section}). */
export type Section = (typeof Section)[keyof typeof Section];

/** Every section in display order. */
export const SECTIONS: readonly Section[] = Object.values(Section);

/** The address fragment and the element id of a section. */
export function sectionId(section: Section): string {
  return `about-${section}`;
}
