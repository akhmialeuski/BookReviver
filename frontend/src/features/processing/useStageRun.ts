import { useState } from 'react';
import type { RunImpactSchema, RunMode, StageRunBody } from '@/api';
import { useRunImpact, useRunInFlight, useRunStage } from '@/features/processing/queries';
import {
  type PageGroup,
  pageIdsFor,
  RunScope,
  type ScopeChoice,
  scopeChoices,
} from '@/features/processing/scope';
import { undoesSplit } from '@/features/processing/split';
import type { Processing } from '@/features/processing/useProcessing';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The run of a stage as the foot of the panel asks for it, whole or up to one step: the scopes with the pages each
 * covers, whether a run can be asked for now, and the request that goes to the server.
 *
 * One instance serves the foot of the panel, which is the only place a run starts from, and the step panel, which reads
 * how many pages the book has. They share what is pending, what failed and the question a run that would send a scan back
 * to one page asks first.
 */

/** What a run is asked to do: over which pages, up to which step, and with what to do with the pages' own work. */
export interface RunRequest {
  scope: RunScope;
  /** The group of pages the scope of a group goes over. */
  group?: PageGroup;
  /** Index in the recipe of the last step to run, or undefined to run through the last step that is on. */
  throughStep?: number;
  /** What the run does with the work of the pages, which is to keep it unless it is asked otherwise. */
  mode?: RunMode;
}

/** What the panel reads of the run of the stage and calls to start one. */
export interface StageRun {
  /** The printed label of the open page, or an empty text when no page is open. */
  pageLabel: string;
  /** How many pages the book has that a run can go over, which are those with an image. */
  total: number;
  /** The choices of the menu with the pages each covers now. */
  choices: readonly ScopeChoice[];
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
   * Give the body of a request for a run, which the count of the pages with work of their own is asked with.
   *
   * @param request What the run is asked to do.
   * @returns The body, or null when there is no recipe or the request covers no page.
   */
  bodyOf: (request: RunRequest) => StageRunBody | null;
  /**
   * Run the saved recipe as asked. A mode that takes work away counts the pages that lose work first, and a run that
   * would take some waits for the answer of the reader. A run that would send a scan back to one page waits for it too.
   *
   * @param request What the run is asked to do.
   */
  start: (request: RunRequest) => void;
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

  const choices = scopeChoices(items, current?.page.id, selected);

  const send = (body: StageRunBody): void =>
    run.mutate({ path: { project_id: projectId, stage }, body });

  const bodyOf = ({
    scope,
    group,
    throughStep,
    mode = 'keep',
  }: RunRequest): StageRunBody | null => {
    if (recipe === undefined) {
      return null;
    }
    // Pages are named by identifier, or by null for every page that has an image
    const ids = pageIdsFor(scope, items, current?.page.id, selected, group);
    if (ids !== null && ids.length === 0) {
      return null;
    }
    // Each page of the scope is run by the recipe of its kind
    return {
      ...(ids === null ? {} : { page_ids: ids }),
      ...(throughStep === undefined ? {} : { through_step: throughStep }),
      ...(mode === 'keep' ? {} : { mode }),
    };
  };

  const start = (request: RunRequest): void => {
    const body = bodyOf(request);
    if (recipe === undefined || body === null) {
      return;
    }
    const affected =
      choices.find((entry) => entry.scope === request.scope && entry.group === request.group)
        ?.items ?? [];
    const proceed = (sent: StageRunBody): void => {
      if (undoesSplit(processing.recipes, affected)) {
        setConfirming(sent);
      } else {
        send(sent);
      }
    };
    if (body.mode === undefined || body.mode === 'keep') {
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

  return {
    pageLabel: current?.page.label ?? '',
    total: choices.find((entry) => entry.scope === RunScope.All)?.items.length ?? 0,
    choices,
    disabled: recipe === undefined || processing.dirty || run.isPending || impact.isPending || busy,
    dirty: processing.dirty,
    busy,
    pending: run.isPending,
    error: run.error ?? impact.error,
    bodyOf,
    start,
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
