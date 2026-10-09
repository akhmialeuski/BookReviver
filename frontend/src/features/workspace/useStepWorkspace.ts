import { useQueries } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import type { CarryOverSchema, FigureState, StagePageSchema, StepPageSchema } from '@/api';
import type { Processing } from '@/features/processing/useProcessing';
import { stepRowOptions, useStepRows } from '@/features/workspace/queries';
import {
  type BarStep,
  barStepsOf,
  countStep,
  hasStepBar,
  openStepOf,
  type StepCounts,
  type StepStates,
} from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The workspace of one step of the open stage: the steps of the recipe for the bar, the step the address names, what that
 * step did on every page of the book, and the state of each step on the open page.
 *
 * The step is the one the address names, so the bar, the canvas and the panel all follow the link. The pages of the book
 * are read once for the open step, which gives the counts and the open page's input and result, and each other step is
 * read for the open page alone, since the dots of the bar want one page of every step and not the whole book of each.
 *
 * What the last carry-over of a step did is kept here, by book, stage and step, because the menu that made it is drawn
 * only for a page whose shape was set by hand and goes away with the page, while its result and undo stay the step's.
 */

/** What the screen reads of the workspace of the open step. */
export interface StepWorkspace {
  /** The saved steps of the recipe shown. */
  steps: readonly BarStep[];
  /** The step the address names, or null when it names none or the recipe shown has no such step. */
  open: BarStep | null;
  /** The state of the shape of each step on the open page; a step still being read is null. */
  states: StepStates;
  /** What the open step read and made on the open page, or null while it is read or when there is no open step. */
  page: StepPageSchema | null;
  /** How the pages of the book stand at the open step, or null while they are read. */
  counts: StepCounts | null;
  /** The rows of every page of the book at the open step, with the flags the server put on them, or null while read. */
  rows: readonly StagePageSchema[] | null;
  /** What the last carry-over of the open step did, or null when there is none, it was taken back, or no step is open. */
  carried: CarryOverSchema | null;
  /** Record what a carry-over of the open step did, or null once it was taken back; it does nothing with no step open. */
  setCarried: (result: CarryOverSchema | null) => void;
}

/**
 * Read the workspace of the open step.
 *
 * @param processing The state of the stage panel, whose recipe is the one shown.
 * @param stepId The identifier of the step the address names, or undefined for a stage with no step open.
 * @param current The page open on the canvas.
 */
export function useStepWorkspace(
  processing: Processing,
  stepId: string | undefined,
  current: StripItem | undefined,
): StepWorkspace {
  const { projectId, stage, recipe, catalogue } = processing;
  // A stage that has no bar reads no row of any step
  const steps = useMemo(
    () => (hasStepBar(stage) ? barStepsOf(recipe, catalogue) : []),
    [stage, recipe, catalogue],
  );
  const open = openStepOf(steps, stepId);
  const rows = useStepRows(projectId, stage, open?.stepId);
  const pageId = current?.page.id;
  const position = current?.page.position;

  const [carriedResults, setCarriedResults] = useState<ReadonlyMap<string, CarryOverSchema>>(
    new Map(),
  );
  const carryKey = open === null ? null : `${projectId}|${stage}|${open.stepId}`;
  const setCarried = useCallback(
    (result: CarryOverSchema | null) => {
      if (carryKey === null) {
        return;
      }
      setCarriedResults((before) => {
        const after = new Map(before);
        if (result === null) {
          after.delete(carryKey);
        } else {
          after.set(carryKey, result);
        }
        return after;
      });
    },
    [carryKey],
  );

  // Every step but the open one is read for the open page alone: one row of the stage at the place of the page
  const others = useQueries({
    queries: steps.map((step) => ({
      ...stepRowOptions(projectId, stage, step.stepId, position ?? 0),
      enabled: position !== undefined && step.stepId !== open?.stepId,
    })),
  });

  const rowOfPage: StagePageSchema | undefined = rows.data?.find((row) => row.page_id === pageId);
  const counts = useMemo(
    () => (rows.data === undefined || open === null ? null : countStep(rows.data)),
    [rows.data, open],
  );
  const states = useMemo(() => {
    const byStep = new Map<string, FigureState | null>();
    steps.forEach((step, index) => {
      // A row read at a place stands for the page only when the page is still there, since pages are moved
      const row = step.stepId === open?.stepId ? rowOfPage : others[index]?.data;
      byStep.set(step.stepId, row?.page_id === pageId ? (row?.step?.state ?? null) : null);
    });
    return byStep;
  }, [steps, open, rowOfPage, others, pageId]);

  return {
    steps,
    open,
    states,
    page: rowOfPage?.step ?? null,
    counts,
    rows: open === null ? null : (rows.data ?? null),
    carried: carryKey === null ? null : (carriedResults.get(carryKey) ?? null),
    setCarried,
  };
}
