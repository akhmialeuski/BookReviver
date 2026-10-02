import type { BookDetailsSchema, ProjectSchema, ProjectUpdate } from '@/api';

/**
 * The fields of the About tab and what it takes to save them.
 *
 * The tab saves by itself, so the form keeps only what the person changed, `edits`, over what the server holds,
 * `saved`. Each change is compared with the saved value, and the differences are sent as a JSON Merge Patch: a field
 * left out keeps its value, and a list is replaced as a whole. A field whose value the server would refuse is not
 * sent at all and is reported as a problem next to its input, so a half-typed language code never reaches it.
 */

/** Longest height of a book the server accepts, in centimetres, and the shortest. */
export const HEIGHT_CM_MAX = 200;
export const HEIGHT_CM_MIN = 1;

const LANGUAGE_CODE = /^[a-z]{3}$/;
const LANGUAGE_SEPARATORS = /[\s,;]+/;

/** Everything the tab edits: the description of the book, its image policy and its cover. */
export type EditableFields = Omit<BookDetailsSchema, 'primary_author'> &
  Pick<ProjectSchema, 'image_policy' | 'cover_page_id'>;

/** The name of one editable field. */
export type FieldKey = keyof EditableFields;

/** The fields typed as one line of free text, which the form builds from one component. */
export type TextKey =
  | 'subtitle'
  | 'original_title'
  | 'publisher'
  | 'printer'
  | 'publication_place'
  | 'publication_year'
  | 'edition'
  | 'censorship'
  | 'series'
  | 'series_number'
  | 'volume'
  | 'printed_pagination'
  | 'illustrations'
  | 'binding'
  | 'copy_holder'
  | 'copy_notes';

/** Every editable field once, which a test keeps in step with {@link EditableFields}. */
export const FIELD_KEYS = [
  'title',
  'subtitle',
  'parallel_titles',
  'original_title',
  'contributors',
  'publisher',
  'printer',
  'publication_place',
  'publication_year',
  'edition',
  'censorship',
  'series',
  'series_number',
  'volume',
  'languages',
  'orthography',
  'script',
  'printed_pagination',
  'height_cm',
  'illustrations',
  'binding',
  'identifiers',
  'subjects',
  'rights',
  'copy_holder',
  'copy_notes',
  'notes',
  'image_policy',
  'cover_page_id',
] as const satisfies readonly FieldKey[];

/** Why a value is not sent, which the tab words next to the field. */
export type Problem =
  | 'title-empty'
  | 'language-code'
  | 'height-range'
  | 'contributor-name'
  | 'identifier-value';

/** The fields that cannot be saved as they stand, each with its reason. */
export type Problems = Partial<Record<FieldKey, Problem>>;

/** What to send for the changes made so far. */
export interface SavePlan {
  /** The fields to send, each with the value the person gave it. */
  readonly patch: ProjectUpdate;
  /** The fields that were changed but are not sent. */
  readonly problems: Problems;
  /** The patch written as text, which tells one set of changes from another. */
  readonly key: string;
}

/**
 * Take the editable fields of a book as the server holds them.
 *
 * @param project The book.
 * @returns Its description without the derived `primary_author`, with its image policy and cover.
 */
export function toFields(project: ProjectSchema): EditableFields {
  const { primary_author: _derived, ...details } = project.details;
  return { ...details, image_policy: project.image_policy, cover_page_id: project.cover_page_id };
}

/** Tell whether two values of fields hold the same, looking inside lists and into the members of a row. */
export function isEqual(left: unknown, right: unknown): boolean {
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((item, index) => isEqual(item, right[index]));
  }
  if (typeof left === 'object' && typeof right === 'object' && left !== null && right !== null) {
    const keys = Object.keys(left);
    return (
      keys.length === Object.keys(right).length &&
      keys.every((key) => isEqual(Reflect.get(left, key), Reflect.get(right, key)))
    );
  }
  return left === right;
}

/** Name the problem the edited value of a field has, or return null when the server would take it. */
function problemOf(key: FieldKey, edits: Partial<EditableFields>): Problem | null {
  switch (key) {
    case 'title':
      return edits.title?.trim() === '' ? 'title-empty' : null;
    case 'languages':
      return edits.languages?.some((code) => !LANGUAGE_CODE.test(code)) ? 'language-code' : null;
    case 'height_cm': {
      const height = edits.height_cm;
      const valid =
        typeof height !== 'number' ||
        (Number.isInteger(height) && height >= HEIGHT_CM_MIN && height <= HEIGHT_CM_MAX);
      return valid ? null : 'height-range';
    }
    case 'contributors':
      return edits.contributors?.some((row) => row.name.trim() === '') ? 'contributor-name' : null;
    case 'identifiers':
      return edits.identifiers?.some((row) => row.value.trim() === '') ? 'identifier-value' : null;
    default:
      return null;
  }
}

function put<K extends FieldKey>(patch: ProjectUpdate, key: K, value: EditableFields[K]): void {
  patch[key] = value;
}

/**
 * Work out what to send for the changes made so far.
 *
 * @param saved The fields as the server holds them.
 * @param edits The fields the person changed, with the value they gave them.
 * @returns The fields that differ from the saved ones and can be saved, and those that differ but cannot.
 */
export function planSave(saved: EditableFields, edits: Partial<EditableFields>): SavePlan {
  const patch: ProjectUpdate = {};
  const problems: Problems = {};
  for (const key of FIELD_KEYS) {
    const value = edits[key];
    if (value === undefined || isEqual(value, saved[key])) {
      continue;
    }
    const problem = problemOf(key, edits);
    if (problem === null) {
      put(patch, key, value);
    } else {
      problems[key] = problem;
    }
  }
  return { patch, problems, key: JSON.stringify(patch) };
}

/** Tell whether a plan has anything to send. */
export function hasChanges(plan: SavePlan): boolean {
  return Object.keys(plan.patch).length > 0;
}

/**
 * Split the text of a field with one entry per line into its entries.
 *
 * @param text The text as typed.
 * @returns The lines without the blank ones, each without surrounding spaces.
 */
export function parseLines(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}

/**
 * Split the text of the languages field into language codes.
 *
 * @param text The text as typed, with codes separated by spaces, commas or semicolons.
 * @returns The codes in lower case, each once.
 */
export function parseCodes(text: string): string[] {
  const codes = text
    .toLowerCase()
    .split(LANGUAGE_SEPARATORS)
    .filter((code) => code !== '');
  return [...new Set(codes)];
}

/**
 * Read the text of the height field.
 *
 * @param text The text as typed.
 * @returns The number it holds, or null for an empty field, which clears the height.
 */
export function parseHeight(text: string): number | null {
  return text.trim() === '' ? null : Number(text);
}
