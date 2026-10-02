import type { BookPlaceSchema, CompareMode, PageFilter, Stage, ViewMode } from '@/api';
import { parseStage, startStage } from '@/features/stages/parse';

/**
 * Chooses the screen a book opens on from the place the reader left it at and from what the book holds now.
 *
 * The place may name a page the reader has deleted since, or a stage that cannot be worked on now. Neither is an error:
 * the book opens on the nearest view that still exists, which is the same stage on its first page, or the stage the
 * book wants worked on next.
 */

/** What the book holds now, which decides whether the place still points at something. */
export interface BookFacts {
  /** The `next_stage` of the book, or null when no stage has work to do. */
  nextStage: Stage | null;
  /** The identifiers of the pages of the book. */
  pageIds: ReadonlySet<string>;
  /** The stages that can be worked on now. */
  availableStages: ReadonlySet<Stage>;
}

/** The search params of a stage that a place restores, with each left out that has its default. */
export interface StageParams {
  page?: string;
  scan?: string;
  source?: string;
  view?: ViewMode;
  compare?: CompareMode;
  filter?: PageFilter;
}

/** Where a book opens: a stage of the workspace, or the reading mode. */
export type ResumeTarget =
  | { mode: 'workspace'; stage: Stage; search: StageParams }
  | { mode: 'reading'; search: { page?: string; spread?: true } };

/**
 * Choose where a book opens.
 *
 * @param place The place the account left the book at, or null when it has not worked on the book.
 * @param facts What the book holds now.
 */
export function resumeTarget(place: BookPlaceSchema | null, facts: BookFacts): ResumeTarget {
  const stage = place === null ? null : parseStage(place.stage);
  if (place === null || stage === null) {
    return { mode: 'workspace', stage: startStage(null, facts.nextStage), search: {} };
  }
  const page =
    place.page_id !== null && facts.pageIds.has(place.page_id) ? place.page_id : undefined;
  if (place.mode === 'reading') {
    return {
      mode: 'reading',
      search: {
        ...(page === undefined ? {} : { page }),
        ...(place.view === 'spread' ? { spread: true } : {}),
      },
    };
  }
  if (!facts.availableStages.has(stage)) {
    return { mode: 'workspace', stage: startStage(null, facts.nextStage), search: {} };
  }
  return {
    mode: 'workspace',
    stage,
    search: {
      ...(page === undefined ? {} : { page }),
      ...(place.scan_id === null ? {} : { scan: place.scan_id }),
      ...(place.source_id === null ? {} : { source: place.source_id }),
      ...(place.view === 'page' ? {} : { view: place.view }),
      ...(place.compare === 'off' ? {} : { compare: place.compare }),
      ...(place.filter === 'all' ? {} : { filter: place.filter }),
    },
  };
}
