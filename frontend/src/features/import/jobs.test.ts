import { describe, expect, it } from 'vitest';
import { job } from '@/features/import/fixtures';
import { importJobsOf } from '@/features/import/jobs';

const NOBODY: ReadonlySet<string> = new Set();

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
    const { active, failed } = importJobsOf(
      [job('run', { kind: 'run-stage' }), job('tiles', { kind: 'cut-tiles', state: 'failed' })],
      NOBODY,
    );
    expect(active).toEqual([]);
    expect(failed).toBeUndefined();
  });

  it('reports the latest ended import when it failed', () => {
    const { failed } = importJobsOf(
      [
        job('first', { state: 'failed', created_at: '2026-10-02T10:00:00Z' }),
        job('second', { state: 'failed', created_at: '2026-10-02T11:00:00Z' }),
      ],
      NOBODY,
    );
    expect(failed?.id).toBe('second');
  });

  it('has no failure for an import that succeeded or was cancelled', () => {
    expect(importJobsOf([job('a', { state: 'succeeded' })], NOBODY).failed).toBeUndefined();
    expect(importJobsOf([job('b', { state: 'cancelled' })], NOBODY).failed).toBeUndefined();
  });

  it('does not fall back to an older failure when the latest import ended well', () => {
    const { failed } = importJobsOf(
      [
        job('old', { state: 'failed', created_at: '2026-10-02T10:00:00Z' }),
        job('clean', { state: 'succeeded', created_at: '2026-10-02T11:00:00Z' }),
      ],
      NOBODY,
    );
    expect(failed).toBeUndefined();
  });

  it('drops the failure the person closed', () => {
    const closed = job('a', { state: 'failed' });
    expect(importJobsOf([closed], new Set(['a'])).failed).toBeUndefined();
  });
});
