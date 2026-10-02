import type { BookDetailsSchema, PageSchema, ProjectSchema } from '@/api';

/**
 * Books and pages for the tests of the About tab: a book described by its title alone and a page of a given kind.
 */

export const EMPTY_DETAILS: BookDetailsSchema = {
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

export function projectWith(details: Partial<BookDetailsSchema> = {}): ProjectSchema {
  return {
    id: 'project-1',
    details: { ...EMPTY_DETAILS, ...details },
    image_policy: 'compact',
    cover_page_id: null,
    page_count: 0,
    source_count: 0,
    scan_count: 0,
    progress: [],
    next_stage: null,
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
  };
}

export function pageOf(id: string, kind: PageSchema['kind'] = 'text'): PageSchema {
  return {
    id,
    position: 0,
    label: '',
    kind,
    origin: 'scan',
    scan_id: null,
    source_id: null,
    slot: 0,
    included: true,
    notes: '',
    group_label: '',
    images: null,
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
  };
}
