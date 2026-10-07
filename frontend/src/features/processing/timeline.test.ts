import { describe, expect, it } from 'vitest';
import type { PageStepChangeSchema } from '@/api';
import { version } from '@/features/processing/fixtures';
import type { HistoryEntry } from '@/features/processing/results';
import { TimelineFilter, timelineOf } from '@/features/processing/timeline';

/** The timeline merges the changes of a step and the results by time, narrows them by a filter, and says how far it is certain. */

function change(id: string, at: string): PageStepChangeSchema {
  return {
    id,
    page_id: 'page',
    stage: 'geometry',
    step_id: 'step',
    layer: 'settings',
    before: null,
    after: { max_angle: 3 },
    source: 'user',
    batch_id: null,
    undoes: null,
    undone: false,
    created_at: at,
    sequence: 1,
  };
}

function result(id: string, at: string, mark: 'good' | 'bad' | null = null): HistoryEntry {
  return { version: version(id, { created_at: at, mark }), current: false };
}

const CHANGES = [
  change('c3', '2026-10-01T12:00:00Z'),
  change('c2', '2026-10-01T10:00:00Z'),
  change('c1', '2026-10-01T08:00:00Z'),
];
const RESULTS = [
  result('r3', '2026-10-01T13:00:00Z', 'bad'),
  result('r2', '2026-10-01T11:00:00Z', 'good'),
  result('r1', '2026-10-01T09:00:00Z'),
];

const ids = (rows: readonly { id: string }[]): string[] => rows.map((row) => row.id);

describe('timelineOf', () => {
  it('merges the changes and the results by time, the newest first, each row keeping its kind', () => {
    const { rows, total } = timelineOf(CHANGES, 3, RESULTS, TimelineFilter.All);

    expect(ids(rows)).toEqual(['r3', 'c3', 'r2', 'c2', 'r1', 'c1']);
    expect(rows.map((row) => row.kind)).toEqual([
      'result',
      'change',
      'result',
      'change',
      'result',
      'change',
    ]);
    expect(total).toBe(6);
  });

  it('puts a result before a change of the same instant, and keeps the order of changes of one instant', () => {
    const same = '2026-10-01T10:00:00Z';
    const { rows } = timelineOf(
      [change('later', same), change('earlier', same)],
      2,
      [result('made', same)],
      TimelineFilter.All,
    );

    expect(ids(rows)).toEqual(['made', 'later', 'earlier']);
  });

  it('compares instants and not the text of the time, so a zone or a fraction does not reorder rows', () => {
    const { rows } = timelineOf(
      [change('c', '2026-10-01T10:00:00.500000+00:00')],
      1,
      [result('r', '2026-10-01T12:00:00+02:00')],
      TimelineFilter.All,
    );

    expect(ids(rows)).toEqual(['c', 'r']);
  });

  it('lists only the changes for Changes and only the results for Results', () => {
    expect(ids(timelineOf(CHANGES, 3, RESULTS, TimelineFilter.Changes).rows)).toEqual([
      'c3',
      'c2',
      'c1',
    ]);
    expect(ids(timelineOf(CHANGES, 3, RESULTS, TimelineFilter.Results).rows)).toEqual([
      'r3',
      'r2',
      'r1',
    ]);
  });

  it('narrows to the results with the mark for Good and for Bad, and leaves the changes out', () => {
    expect(ids(timelineOf(CHANGES, 3, RESULTS, TimelineFilter.Good).rows)).toEqual(['r2']);
    expect(ids(timelineOf(CHANGES, 3, RESULTS, TimelineFilter.Bad).rows)).toEqual(['r3']);
    expect(timelineOf(CHANGES, 3, RESULTS, TimelineFilter.Bad).total).toBe(1);
  });

  it('is certain all the way when every change is loaded', () => {
    const timeline = timelineOf(CHANGES, 3, RESULTS, TimelineFilter.All);

    expect(timeline.more).toBe(false);
    expect(timeline.certain).toBe(6);
  });

  it('is certain only down to the oldest change loaded when older changes are not loaded', () => {
    const loaded = CHANGES.slice(0, 2);

    const timeline = timelineOf(loaded, 3, RESULTS, TimelineFilter.All);

    // r1 is older than c2, so a change that is not loaded could stand above it
    expect(ids(timeline.rows)).toEqual(['r3', 'c3', 'r2', 'c2', 'r1']);
    expect(timeline.more).toBe(true);
    expect(timeline.certain).toBe(4);
    expect(timeline.total).toBe(6);
  });

  it('is certain of nothing while no change is loaded though some exist', () => {
    const timeline = timelineOf([], 3, RESULTS, TimelineFilter.All);

    expect(timeline.more).toBe(true);
    expect(timeline.certain).toBe(0);
  });

  it('is certain all the way and wants no more changes when the filter lets no change through', () => {
    const loaded = CHANGES.slice(0, 1);

    for (const filter of [TimelineFilter.Results, TimelineFilter.Good, TimelineFilter.Bad]) {
      const timeline = timelineOf(loaded, 3, RESULTS, filter);

      expect(timeline.more).toBe(false);
      expect(timeline.certain).toBe(timeline.rows.length);
    }
  });

  it('is empty for a step with nothing on the page', () => {
    expect(timelineOf([], 0, [], TimelineFilter.All)).toEqual({
      rows: [],
      certain: 0,
      total: 0,
      more: false,
    });
  });
});
