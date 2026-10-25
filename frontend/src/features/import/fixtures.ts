import type { JobSchema, ScanSchema, SourceSchema } from '@/api';
import type { ImportedSource } from '@/features/import/queries';
import { images } from '@/features/workspace/fixtures';

/**
 * Files, scans and jobs as the tests of the Import stage build them, with every field filled and any of them changed.
 */

/** A scan of a file, a 400 dpi page with its images cut. */
export function scan(id: string, overrides: Partial<ScanSchema> = {}): ScanSchema {
  return {
    id,
    source_id: 'f-1',
    number: 0,
    source_label: '',
    facts: {
      width_px: 2400,
      height_px: 3200,
      color_mode: 'color',
      dpi_x: 400,
      dpi_y: 400,
      bits_per_component: 8,
      image_format: 'JPEG',
      width_mm: null,
      height_mm: null,
      has_text_layer: false,
      extra: {},
    },
    images: images(`scan-${id}`),
    ...overrides,
  };
}

/** A file of the book, a PDF of 96 scans that suggests nothing for the description. */
export function source(id: string, overrides: Partial<ImportedSource> = {}): ImportedSource {
  return {
    id,
    kind: 'pdf',
    file_type: 'pdf',
    file_name: `${id}.pdf`,
    files: [],
    size_bytes: 860_000_000,
    sha256: id,
    scan_count: 96,
    metadata: {},
    suggestion: {
      title: '',
      contributors: [],
      publisher: '',
      publication_year: '',
      languages: [],
      identifiers: [],
      subjects: [],
    },
    status: 'imported',
    rejection: null,
    detail: '',
    imported_at: '2026-10-02T10:14:00Z',
    ...overrides,
  };
}

/** A file of the report of an import, one that was rejected or skipped and so has no kind, type or scans. */
export function unimported(
  id: string,
  status: 'rejected' | 'skipped',
  overrides: Partial<SourceSchema> = {},
): SourceSchema {
  return {
    ...source(id),
    kind: null,
    file_type: null,
    size_bytes: 0,
    scan_count: 0,
    status,
    ...overrides,
  };
}

/** A job of the book, an import that is running 12 steps of 18. */
export function job(id: string, overrides: Partial<JobSchema> = {}): JobSchema {
  return {
    id,
    kind: 'import-source',
    stage: null,
    state: 'running',
    progress: { done: 12, total: 18, fraction: 12 / 18 },
    error: '',
    created_at: '2026-10-02T10:00:00Z',
    started_at: '2026-10-02T10:00:05Z',
    finished_at: null,
    ...overrides,
  };
}
