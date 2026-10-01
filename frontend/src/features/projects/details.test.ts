import { describe, expect, it } from 'vitest';
import type { BookDetailsSchema } from '@/api';
import { detailRows } from './details';

/**
 * The rows of the book description: empty and unknown fields are left out.
 */

const EMPTY: BookDetailsSchema = {
  title: 'A title',
  subtitle: '',
  parallel_titles: [],
  original_title: '',
  contributors: [],
  primary_author: '',
  publisher: '',
  printer: '',
  publication_place: '',
  publication_year: '',
  edition: '',
  censorship: '',
  series: '',
  series_number: '',
  volume: '',
  languages: [],
  orthography: 'unknown',
  script: 'unknown',
  printed_pagination: '',
  height_cm: null,
  illustrations: '',
  binding: '',
  identifiers: [],
  subjects: [],
  rights: 'unknown',
  copy_holder: '',
  copy_notes: '',
  notes: '',
};

describe('detailRows', () => {
  it('returns no rows for a book described by its title alone', () => {
    expect(detailRows(EMPTY)).toEqual([]);
  });

  it('returns the filled fields in display order with readable values', () => {
    const rows = detailRows({
      ...EMPTY,
      primary_author: 'Yanka Kupala',
      publication_year: '1913',
      languages: ['bel', 'rus'],
      orthography: 'pre-reform',
    });

    expect(rows).toEqual([
      { label: 'Author', value: 'Yanka Kupala' },
      { label: 'Year', value: '1913' },
      { label: 'Languages', value: 'bel, rus' },
      { label: 'Orthography', value: 'Pre-reform' },
    ]);
  });

  it('ignores a value that is only whitespace', () => {
    expect(detailRows({ ...EMPTY, notes: '   ' })).toEqual([]);
  });
});
