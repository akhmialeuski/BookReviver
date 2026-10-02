import type { MetadataSuggestionSchema } from '@/api';
import { type EditableFields, isEqual } from './fields';

/**
 * What a file of the book suggests for its description, and what using the suggestion changes.
 *
 * The import has already filled the empty fields of the description from the files, so a suggestion matters when it
 * differs from what the description says: another title, a publisher the person wrote otherwise, an author the list
 * does not have. Using it replaces the single-valued fields and adds the entries of the lists that are missing, so
 * a list the person wrote by hand never loses an entry.
 */

/** The fields of the description a file can suggest a value for. */
export const SUGGESTED_FIELDS = [
  'title',
  'contributors',
  'publisher',
  'publication_year',
  'languages',
  'identifiers',
  'subjects',
] as const satisfies readonly (keyof MetadataSuggestionSchema)[];

/** The name of one suggested field. */
export type SuggestedField = (typeof SUGGESTED_FIELDS)[number];

/** One field a suggestion would change, with what it would put there. */
export interface SuggestionRow {
  readonly field: SuggestedField;
  readonly text: string;
}

/** What using a suggestion does to the description. */
export interface SuggestionPlan {
  /** The new values of the fields it changes. */
  readonly changes: Partial<EditableFields>;
  /** The same changes in words, for the person to read before they use it. */
  readonly rows: SuggestionRow[];
}

const SCALARS = ['title', 'publisher', 'publication_year'] as const;

// British English writes no comma before the last "and", as the sentences of the interface do
const listFormat = new Intl.ListFormat('en-GB', { style: 'long', type: 'conjunction' });

/**
 * Name what a suggestion changes in one phrase, such as `a title, a year and contributors`.
 *
 * @param rows The changes the suggestion makes.
 * @param names How a sentence names each suggested field.
 * @returns The names in the order of the rows, joined the way a sentence lists them.
 */
export function suggestionSummary(
  rows: readonly SuggestionRow[],
  names: Record<SuggestedField, string>,
): string {
  return listFormat.format(rows.map((row) => names[row.field]));
}

function missingFrom<T>(current: readonly T[], suggested: readonly T[]): T[] {
  return suggested.filter((item) => !current.some((have) => isEqual(have, item)));
}

/**
 * Work out what using a suggestion would change.
 *
 * @param current The description as the form shows it now.
 * @param suggestion What a file of the book suggests.
 * @returns The fields it changes with their new values, and the same in words. Nothing when it adds nothing.
 */
export function planSuggestion(
  current: EditableFields,
  suggestion: MetadataSuggestionSchema,
): SuggestionPlan {
  const changes: Partial<EditableFields> = {};
  const rows: SuggestionRow[] = [];

  for (const field of SCALARS) {
    const value = suggestion[field];
    if (value.trim() !== '' && value !== current[field]) {
      changes[field] = value;
      rows.push({ field, text: value });
    }
  }

  const contributors = missingFrom(current.contributors, suggestion.contributors);
  if (contributors.length > 0) {
    changes.contributors = [...current.contributors, ...contributors];
    rows.push({ field: 'contributors', text: contributors.map((entry) => entry.name).join('; ') });
  }
  const languages = missingFrom(current.languages, suggestion.languages);
  if (languages.length > 0) {
    changes.languages = [...current.languages, ...languages];
    rows.push({ field: 'languages', text: languages.join(', ') });
  }
  const identifiers = missingFrom(current.identifiers, suggestion.identifiers);
  if (identifiers.length > 0) {
    changes.identifiers = [...current.identifiers, ...identifiers];
    rows.push({ field: 'identifiers', text: identifiers.map((entry) => entry.value).join('; ') });
  }
  const subjects = missingFrom(current.subjects, suggestion.subjects);
  if (subjects.length > 0) {
    changes.subjects = [...current.subjects, ...subjects];
    rows.push({ field: 'subjects', text: subjects.join('; ') });
  }

  return { changes, rows };
}
