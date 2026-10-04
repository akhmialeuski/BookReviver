import type { ImagePathsSchema, PageSchema, PaginationSectionSchema, StagePageSchema } from '@/api';

/**
 * Pages and rows of a stage as the tests of the workspace build them, with every field filled and any of them changed.
 */

/** The images of a page or of a result, all under one name. */
export function images(name: string): ImagePathsSchema {
  return {
    full: `/${name}/full`,
    preview: `/${name}/preview`,
    thumbnail: `/${name}/thumb`,
    iiif_info: `/${name}/info.json`,
  };
}

/** A page of the book. */
export function page(id: string, overrides: Partial<PageSchema> = {}): PageSchema {
  return {
    id,
    position: 0,
    label: '',
    label_manual: false,
    section_id: null,
    kind: 'text',
    origin: 'scan',
    scan_id: null,
    source_id: null,
    slot: 0,
    included: true,
    notes: '',
    group_label: '',
    blank_fill: 'scan',
    images: images(`page-${id}`),
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

/** A pagination section of a book that starts at a page and prints Arabic numbers from 1 unless told otherwise. */
export function section(
  id: string,
  firstPageId: string,
  overrides: Partial<PaginationSectionSchema> = {},
): PaginationSectionSchema {
  return {
    id,
    project_id: 'book',
    first_page_id: firstPageId,
    name: '',
    style: 'arabic',
    start: 1,
    prefix: '',
    display: 'printed',
    kinds: [],
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

/** The row of a page in a stage, up to date unless told otherwise. */
export function row(id: string, overrides: Partial<StagePageSchema> = {}): StagePageSchema {
  return {
    page_id: id,
    status: 'fresh',
    review: null,
    recipe_id: null,
    pinned: false,
    version: null,
    through_step: null,
    review_processor: null,
    step: null,
    ...overrides,
  };
}
