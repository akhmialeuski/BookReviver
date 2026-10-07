import type { PageStepChangeSchema } from '@/api';
import type { HistoryEntry } from '@/features/processing/results';

/**
 * The timeline of a page: the changes of a step and the results of a step or of a stage merged into one list by time,
 * newest first, and narrowed by a filter.
 *
 * The changes come from the server a page at a time, the newest first, while the results are all read at once. A result
 * older than the oldest change loaded so far could still be preceded by a change that is not loaded, so only the rows
 * down to that change are certain, and the list says how many those are.
 */

/** What the reader narrows the timeline to. */
export const TimelineFilter = {
  All: 'all',
  Changes: 'changes',
  Results: 'results',
  Good: 'good',
  Bad: 'bad',
} as const;

/** One filter of the timeline (derived from {@link TimelineFilter}). */
export type TimelineFilter = (typeof TimelineFilter)[keyof typeof TimelineFilter];

/** The filters in the order the row of them lists them. */
export const TIMELINE_FILTERS: readonly TimelineFilter[] = Object.values(TimelineFilter);

/** One row of the timeline: a change of the history or a result. */
export type TimelineRow =
  | { kind: 'change'; id: string; at: string; change: PageStepChangeSchema }
  | { kind: 'result'; id: string; at: string; entry: HistoryEntry };

/** The rows a filter lets through, and what is known about the rows that are not loaded. */
export interface Timeline {
  /** The rows loaded, the newest first. */
  rows: TimelineRow[];
  /** How many of the first rows are certain: no change that is not loaded can stand among them. */
  certain: number;
  /** How many rows the filter lets through in all, loaded or not. */
  total: number;
  /** Whether changes older than the loaded ones exist and the filter lets them through. */
  more: boolean;
}

/**
 * Merge the changes and the results into one list by time and narrow it by a filter.
 *
 * A result and a change of one instant stand result first, since a result is made after the change that asked for it.
 * The changes keep the order the server gave them, which is the order they were written in.
 *
 * @param changes The changes loaded so far, the newest first.
 * @param changesTotal How many changes the step has on the page in all.
 * @param results The results of the step or of the stage, the newest first.
 * @param filter What the reader narrows the list to; Good and Bad keep the results with that mark.
 */
export function timelineOf(
  changes: readonly PageStepChangeSchema[],
  changesTotal: number,
  results: readonly HistoryEntry[],
  filter: TimelineFilter,
): Timeline {
  const withChanges = filter === TimelineFilter.All || filter === TimelineFilter.Changes;
  const withResults = filter !== TimelineFilter.Changes;
  const mark = filter === TimelineFilter.Good || filter === TimelineFilter.Bad ? filter : null;
  const entries = withResults
    ? results.filter(({ version }) => mark === null || version.mark === mark)
    : [];
  const rows: TimelineRow[] = [
    ...entries.map(
      (entry): TimelineRow => ({
        kind: 'result',
        id: entry.version.id,
        at: entry.version.created_at,
        entry,
      }),
    ),
    ...(withChanges
      ? changes.map(
          (change): TimelineRow => ({
            kind: 'change',
            id: change.id,
            at: change.created_at,
            change,
          }),
        )
      : []),
  ].toSorted((a, b) => Date.parse(b.at) - Date.parse(a.at));

  const more = withChanges && changes.length < changesTotal;
  const oldestLoaded = changes.at(-1);
  // Every row at least as new as the oldest change loaded is certain, and when none is loaded none is
  const certain = !more
    ? rows.length
    : oldestLoaded === undefined
      ? 0
      : rows.filter((row) => Date.parse(row.at) >= Date.parse(oldestLoaded.created_at)).length;
  return {
    rows,
    certain,
    total: entries.length + (withChanges ? changesTotal : 0),
    more,
  };
}
