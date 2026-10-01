import type { BookDetailsSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * The description of a book as a list of labelled rows for the book page.
 *
 * Only the fields that have a value are shown, so a book described by its title alone does not render a wall of
 * empty rows, and the enumerations whose value is `unknown` count as empty.
 */

const UNKNOWN = 'unknown';

export interface DetailRow {
  label: string;
  value: string;
}

/** Return the rows of the filled-in fields of a description, in the order the screen shows them. */
export function detailRows(details: BookDetailsSchema): DetailRow[] {
  const fields = MESSAGES.book.fields;
  const candidates: DetailRow[] = [
    { label: fields.subtitle, value: details.subtitle },
    { label: fields.author, value: details.primary_author },
    { label: fields.publisher, value: details.publisher },
    { label: fields.place, value: details.publication_place },
    { label: fields.year, value: details.publication_year },
    { label: fields.edition, value: details.edition },
    { label: fields.languages, value: details.languages.join(', ') },
    {
      label: fields.orthography,
      value: details.orthography === UNKNOWN ? '' : MESSAGES.book.orthography[details.orthography],
    },
    {
      label: fields.script,
      value: details.script === UNKNOWN ? '' : MESSAGES.book.script[details.script],
    },
    { label: fields.notes, value: details.notes },
  ];
  return candidates.filter((row) => row.value.trim() !== '');
}
