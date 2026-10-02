import { describe, expect, it } from 'vitest';
import type { BookPlaceSchema, Stage } from '@/api';
import { type BookFacts, resumeTarget } from '@/features/place/resume';
import { STAGES } from '@/features/stages/stages';

const EVERY_STAGE: ReadonlySet<Stage> = new Set(STAGES.map((entry) => entry.stage));

function facts(extra: Partial<BookFacts> = {}): BookFacts {
  return {
    nextStage: 'page-order',
    pageIds: new Set(['p-1', 'p-2']),
    availableStages: EVERY_STAGE,
    ...extra,
  };
}

function placeOf(extra: Partial<BookPlaceSchema> = {}): BookPlaceSchema {
  return {
    mode: 'workspace',
    stage: 'geometry',
    page_id: 'p-2',
    scan_id: null,
    source_id: null,
    view: 'page',
    compare: 'off',
    filter: 'all',
    canvas: null,
    strip_page_id: null,
    updated_at: '2026-10-02T10:00:00Z',
    ...extra,
  };
}

describe('resumeTarget', () => {
  it('opens a book that was not worked on on its next stage', () => {
    expect(resumeTarget(null, facts())).toEqual({
      mode: 'workspace',
      stage: 'page-order',
      search: {},
    });
  });

  it('opens a book with neither a place nor a next stage on the first stage', () => {
    expect(resumeTarget(null, facts({ nextStage: null }))).toEqual({
      mode: 'workspace',
      stage: 'import',
      search: {},
    });
  });

  it('opens the stage, the page and the view the place names', () => {
    const place = placeOf({ view: 'spread', compare: 'swipe', filter: 'check' });
    expect(resumeTarget(place, facts())).toEqual({
      mode: 'workspace',
      stage: 'geometry',
      search: { page: 'p-2', view: 'spread', compare: 'swipe', filter: 'check' },
    });
  });

  it('leaves out what has its default, so the link stays short', () => {
    expect(resumeTarget(placeOf(), facts())).toEqual({
      mode: 'workspace',
      stage: 'geometry',
      search: { page: 'p-2' },
    });
  });

  it('keeps the file and the scan of the stages that work on files', () => {
    const place = placeOf({ stage: 'import', page_id: null, scan_id: 's-3', source_id: 'f-1' });
    expect(resumeTarget(place, facts()).mode).toBe('workspace');
    expect(resumeTarget(place, facts())).toMatchObject({
      stage: 'import',
      search: { scan: 's-3', source: 'f-1' },
    });
  });

  it('opens the same stage on its first page when the page was deleted', () => {
    const place = placeOf({ page_id: 'p-deleted', view: 'grid' });
    expect(resumeTarget(place, facts())).toEqual({
      mode: 'workspace',
      stage: 'geometry',
      search: { view: 'grid' },
    });
  });

  it('opens the next stage when the stage of the place cannot be worked on', () => {
    const available: ReadonlySet<Stage> = new Set(['import', 'page-order']);
    expect(resumeTarget(placeOf(), facts({ availableStages: available }))).toEqual({
      mode: 'workspace',
      stage: 'page-order',
      search: {},
    });
  });

  it('opens the next stage when the place names a stage this version does not know', () => {
    const place = placeOf({ stage: 'ocr' as Stage });
    expect(resumeTarget(place, facts()).mode).toBe('workspace');
    expect(resumeTarget(place, facts())).toMatchObject({ stage: 'page-order', search: {} });
  });

  it('opens the reading mode on the page the reader left', () => {
    expect(resumeTarget(placeOf({ mode: 'reading', view: 'spread' }), facts())).toEqual({
      mode: 'reading',
      search: { page: 'p-2', spread: true },
    });
  });

  it('opens the reading mode on the first page when the page was deleted', () => {
    expect(resumeTarget(placeOf({ mode: 'reading', page_id: 'gone' }), facts())).toEqual({
      mode: 'reading',
      search: {},
    });
  });

  it('opens the reading mode whatever the stage the reader came from can do', () => {
    const place = placeOf({ mode: 'reading' });
    expect(resumeTarget(place, facts({ availableStages: new Set<Stage>() })).mode).toBe('reading');
  });
});
