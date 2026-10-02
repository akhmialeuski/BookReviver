import { describe, expect, it } from 'vitest';
import type { ProjectSchema, StageStatus } from '@/api';
import { nextStageOf, progressSegments } from './progress';
import { STAGES } from './stages';

/**
 * The ten segments of a book and its next stage, as the library card reads them.
 */

function progressOf(statuses: Partial<Record<string, StageStatus>>): ProjectSchema['progress'] {
  return STAGES.map(({ stage }) => ({ stage, status: statuses[stage] ?? 'waiting' }));
}

describe('progressSegments', () => {
  it('gives ten segments in pipeline order with the status of each stage', () => {
    const segments = progressSegments(progressOf({ import: 'done', 'page-split': 'attention' }));

    expect(segments.map((segment) => segment.stage)).toEqual(STAGES.map((entry) => entry.stage));
    expect(segments.slice(0, 3).map((segment) => segment.status)).toEqual([
      'done',
      'attention',
      'waiting',
    ]);
  });

  it('puts the stages in pipeline order when the server lists them in another', () => {
    const reversed = [...progressOf({ typesetting: 'done' })].reverse();

    const segments = progressSegments(reversed);

    expect(segments[0]?.stage).toBe('import');
    expect(segments[9]).toEqual({ stage: 'typesetting', status: 'done' });
  });

  it('draws a stage the book has no entry for as not available', () => {
    const segments = progressSegments([{ stage: 'import', status: 'done' }]);

    expect(segments).toHaveLength(10);
    expect(segments[1]).toEqual({ stage: 'page-split', status: 'unavailable' });
  });
});

describe('nextStageOf', () => {
  it('gives the next stage with its status', () => {
    const progress = progressOf({ import: 'done', 'page-split': 'done', 'page-order': 'done' });

    expect(nextStageOf({ progress, next_stage: 'geometry' })).toEqual({
      stage: 'geometry',
      status: 'waiting',
    });
  });

  it('gives the status the stage has when it needs a look', () => {
    const progress = progressOf({ import: 'done', 'page-split': 'attention' });

    expect(nextStageOf({ progress, next_stage: 'page-split' })).toEqual({
      stage: 'page-split',
      status: 'attention',
    });
  });

  it('gives nothing when everything that can be done is done', () => {
    expect(nextStageOf({ progress: progressOf({}), next_stage: null })).toBeNull();
  });
});
