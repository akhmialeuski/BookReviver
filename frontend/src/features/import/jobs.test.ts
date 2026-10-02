import { describe, expect, it } from 'vitest';
import type { ImportResultSchema } from '@/api';
import { job } from '@/features/import/fixtures';
import { importJobsOf } from '@/features/import/jobs';

const NOBODY: ReadonlySet<string> = new Set();

function result(overrides: Partial<ImportResultSchema> = {}): ImportResultSchema {
  return { imported: ['f-1'], rejected: [], skipped: [], ...overrides };
}

const REJECTED = [{ file_name: 'broken.png', reason: 'unreadable', detail: '' }] as const;

describe('importJobsOf', () => {
  it('keeps the imports that run, the oldest first', () => {
    const { active } = importJobsOf(
      [
        job('new', { created_at: '2026-10-02T10:05:00Z' }),
        job('old', { state: 'queued', created_at: '2026-10-02T10:01:00Z' }),
      ],
      NOBODY,
    );
    expect(active.map((entry) => entry.id)).toEqual(['old', 'new']);
  });

  it('leaves out the jobs that are not imports', () => {
    const { active, report } = importJobsOf(
      [job('run', { kind: 'run-stage' }), job('tiles', { kind: 'cut-tiles', state: 'failed' })],
      NOBODY,
    );
    expect(active).toEqual([]);
    expect(report).toBeUndefined();
  });

  it('reports the latest ended import that rejected files', () => {
    const { report } = importJobsOf(
      [
        job('first', {
          state: 'succeeded',
          created_at: '2026-10-02T10:00:00Z',
          result: result({ skipped: ['x.png'] }),
        }),
        job('second', {
          state: 'succeeded',
          created_at: '2026-10-02T11:00:00Z',
          result: result({ rejected: [...REJECTED] }),
        }),
      ],
      NOBODY,
    );
    expect(report?.id).toBe('second');
  });

  it('reports a failed import and a cancelled one that skipped files', () => {
    expect(importJobsOf([job('a', { state: 'failed' })], NOBODY).report?.id).toBe('a');
    expect(
      importJobsOf(
        [job('b', { state: 'cancelled', result: result({ skipped: ['x.png'] }) })],
        NOBODY,
      ).report?.id,
    ).toBe('b');
  });

  it('has nothing to report for an import that took every file', () => {
    expect(
      importJobsOf([job('a', { state: 'succeeded', result: result() })], NOBODY).report,
    ).toBeUndefined();
  });

  it('does not fall back to an older report when the latest import was clean', () => {
    const { report } = importJobsOf(
      [
        job('old', {
          state: 'succeeded',
          created_at: '2026-10-02T10:00:00Z',
          result: result({ rejected: [...REJECTED] }),
        }),
        job('clean', {
          state: 'succeeded',
          created_at: '2026-10-02T11:00:00Z',
          result: result(),
        }),
      ],
      NOBODY,
    );
    expect(report).toBeUndefined();
  });

  it('drops the report the person closed', () => {
    const failed = job('a', { state: 'failed' });
    expect(importJobsOf([failed], new Set(['a'])).report).toBeUndefined();
  });
});
