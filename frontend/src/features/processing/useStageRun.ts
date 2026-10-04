import { useState } from 'react';
import type { RunImpactSchema, RunMode, StageRunBody } from '@/api';
import { useRunImpact, useRunInFlight, useRunStage } from '@/features/processing/queries';
import {
  describeScope,
  pageIdsFor,
  type RunScope,
  type ScopeChoice,
  scopeChoices,
} from '@/features/processing/scope';
import { undoesSplit } from '@/features/processing/split';
import type { Processing } from '@/features/processing/useProcessing';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The run of a stage as the panel asks for it, whole or up to one step: the scopes with the pages each covers, whether a
 * run can be asked for now, and the request that goes to the server.
 *
 * One instance serves the foot of the panel and the steps of the recipe, so the two share what is pending, what failed
 * and the question a run that would send a scan back to one page asks first.
 */

/** What the panel reads of the run of the stage and calls to start one. */
export interface StageRun {
  /** The scopes of the menu with the number of pages each covers now. */
  choices: readonly ScopeChoice[];
  /** Write a scope of the menu with the number of pages it covers. */
  describe: (scope: RunScope, count: number) => string;
  /** Whether a run cannot be asked for: there is no recipe, the draft has changes, or the request is on its way. */
  disabled: boolean;
  /** Whether the draft has changes, so the run waits for them to be saved. */
  dirty: boolean;
  /** Whether another job of the book is going, which the run waits for. */
  busy: boolean;
  pending: boolean;
  /** What the server answered when it refused the last run, or null. */
  error: unknown;
  /**
   * Run the saved recipe over a scope.
   *
   * @param scope The pages to run it on.
   * @param throughStep Index in the recipe of the last step to run, or undefined to run through the last step that is on.
   * @param mode What the run does with the settings and the hand edits of the pages, which is to keep them unless it is
   * asked to take them away. A mode that takes them away counts the pages that lose work first, and a run that would
   * take some waits for the answer of the reader.
   */
  start: (scope: RunScope, throughStep?: number, mode?: RunMode) => void;
  /**
   * Run the saved recipe over the pages named, up to a step.
   *
   * @param pageIds The pages to run it on.
   * @param throughStep Index in the recipe of the last step to run.
   */
  startPages: (pageIds: readonly string[], throughStep: number) => void;
  /** Whether a run that would delete the right half of a spread waits for the answer of the reader. */
  confirming: boolean;
  confirm: () => void;
  cancel: () => void;
  /** What a run in a mode that takes work away would take, while it waits for the answer of the reader, or null. */
  overwriting: RunImpactSchema | null;
  confirmOverwrite: () => void;
  cancelOverwrite: () => void;
}

/**
 * Read the run of a stage.
 *
 * @param processing The recipe shown in the panel and whether its draft is saved.
 * @param items Every page of the book with where it stands in the stage.
 * @param current The page open on the canvas.
 * @param selected The pages selected in the grid.
 */
export function useStageRun(
  processing: Processing,
  items: readonly StripItem[],
  current: StripItem | undefined,
  selected: ReadonlySet<string>,
): StageRun {
  const { projectId, stage, recipe } = processing;
  const run = useRunStage(projectId, stage);
  const activeJobs = useActiveJobs(projectId);
  const runInFlight = useRunInFlight(projectId);
  const impact = useRunImpact();
  const [confirming, setConfirming] = useState<StageRunBody | null>(null);
  const [overwriting, setOverwriting] = useState<{
    impact: RunImpactSchema;
    proceed: () => void;
  } | null>(null);
  const busy = (activeJobs.data?.length ?? 0) > 0 || runInFlight;

  const send = (body: StageRunBody): void =>
    run.mutate({ path: { project_id: projectId, stage }, body });

  // Pages are named by identifier, or by null for every page that has an image
  const begin = (
    ids: readonly string[] | null,
    throughStep?: number,
    mode: RunMode = 'keep',
  ): void => {
    if (recipe === undefined) {
      return;
    }
    // The active recipe is the book's own: each page then gets the variant it is pinned to or the rules choose. Any
    // other variant is a trial, and goes to every page of the scope
    const body: StageRunBody = {
      ...(recipe.active ? {} : { recipe_id: recipe.id }),
      ...(ids === null ? {} : { page_ids: [...ids] }),
      ...(throughStep === undefined ? {} : { through_step: throughStep }),
      ...(mode === 'keep' ? {} : { mode }),
    };
    const affected =
      ids === null
        ? items.map((item) => item.page)
        : items.filter((item) => ids.includes(item.page.id)).map((item) => item.page);
    const proceed = (sent: StageRunBody): void => {
      if (undoesSplit(recipe, affected)) {
        setConfirming(sent);
      } else {
        send(sent);
      }
    };
    if (mode === 'keep') {
      proceed(body);
      return;
    }
    // A mode that takes work away asks first, with the number of pages it takes it from, unless it takes none
    impact.mutate(
      { path: { project_id: projectId, stage }, body },
      {
        onSuccess: (counted) => {
          if (counted.affected === 0) {
            proceed(body);
          } else {
            setOverwriting({
              impact: counted,
              proceed: () => proceed({ ...body, confirm_overwrite: true }),
            });
          }
        },
      },
    );
  };

  const start = (scope: RunScope, throughStep?: number, mode?: RunMode): void =>
    begin(pageIdsFor(scope, items, current?.page.id, selected), throughStep, mode);

  return {
    choices: scopeChoices(items, current?.page.id, selected),
    describe: (scope, count) => describeScope(scope, count, current?.page.label ?? ''),
    disabled: recipe === undefined || processing.dirty || run.isPending || impact.isPending || busy,
    dirty: processing.dirty,
    busy,
    pending: run.isPending,
    error: run.error ?? impact.error,
    start,
    startPages: begin,
    confirming: confirming !== null,
    confirm: () => {
      if (confirming !== null) {
        send({ ...confirming, confirm_unsplit: true });
      }
      setConfirming(null);
    },
    cancel: () => setConfirming(null),
    overwriting: overwriting?.impact ?? null,
    confirmOverwrite: () => {
      overwriting?.proceed();
      setOverwriting(null);
    },
    cancelOverwrite: () => setOverwriting(null),
  };
}
