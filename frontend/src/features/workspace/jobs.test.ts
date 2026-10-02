import { describe, expect, it } from 'vitest';
import type { JobSchema } from '@/api';
import { formatTime, isStoppable, jobMoment, latestActiveJob } from '@/features/workspace/jobs';

function job(id: string, state: JobSchema['state'], startedAt: string | null): JobSchema {
  return {
    id,
    project_id: 'p-1',
    kind: 'run-stage',
    state,
    progress: { done: 1, total: 4, fraction: 0.25 },
    error: '',
    result: null,
    created_at: '2026-10-01T10:00:00Z',
    started_at: startedAt,
    finished_at: null,
  };
}

describe('latestActiveJob', () => {
  it('is nothing when no job is queued or running', () => {
    expect(latestActiveJob([])).toBeUndefined();
    expect(
      latestActiveJob([
        job('a', 'succeeded', '2026-10-01T10:05:00Z'),
        job('b', 'failed', '2026-10-01T10:06:00Z'),
        job('c', 'cancelled', null),
      ]),
    ).toBeUndefined();
  });

  it('picks the most recently started running job', () => {
    const picked = latestActiveJob([
      job('old', 'running', '2026-10-01T10:05:00Z'),
      job('new', 'running', '2026-10-01T10:30:00Z'),
      job('done', 'succeeded', '2026-10-01T10:50:00Z'),
    ]);
    expect(picked?.id).toBe('new');
  });

  it('prefers a running job to a queued one that was recorded later', () => {
    const picked = latestActiveJob([
      job('waiting', 'queued', null),
      job('busy', 'running', '2026-10-01T09:00:00Z'),
    ]);
    expect(picked?.id).toBe('busy');
  });

  it('falls back to the newest queued job', () => {
    const picked = latestActiveJob([
      { ...job('first', 'queued', null), created_at: '2026-10-01T10:01:00Z' },
      { ...job('second', 'queued', null), created_at: '2026-10-01T10:02:00Z' },
    ]);
    expect(picked?.id).toBe('second');
  });

  it('leaves the list it was given in its order', () => {
    const jobs = [
      job('b', 'running', '2026-10-01T10:30:00Z'),
      job('a', 'running', '2026-10-01T10:05:00Z'),
    ];
    latestActiveJob(jobs);
    expect(jobs.map((entry) => entry.id)).toEqual(['b', 'a']);
  });
});

describe('isStoppable', () => {
  it('is true for a queued and a running job only', () => {
    expect(isStoppable({ state: 'queued' })).toBe(true);
    expect(isStoppable({ state: 'running' })).toBe(true);
    for (const state of ['succeeded', 'failed', 'cancelled'] as const) {
      expect(isStoppable({ state })).toBe(false);
    }
  });
});

describe('jobMoment', () => {
  it('dates a job by its end, then its start, then its record', () => {
    const base = job('a', 'running', null);
    expect(jobMoment(base)).toBe('2026-10-01T10:00:00Z');
    expect(jobMoment({ ...base, started_at: '2026-10-01T10:05:00Z' })).toBe('2026-10-01T10:05:00Z');
    expect(
      jobMoment({
        ...base,
        started_at: '2026-10-01T10:05:00Z',
        finished_at: '2026-10-01T10:09:00Z',
      }),
    ).toBe('2026-10-01T10:09:00Z');
  });
});

describe('formatTime', () => {
  it('writes a short time of day', () => {
    expect(formatTime('2026-10-01T10:42:00')).toMatch(/10:42/);
  });

  it('is empty for no timestamp and for text that is no date', () => {
    expect(formatTime(null)).toBe('');
    expect(formatTime('yesterday')).toBe('');
  });
});
