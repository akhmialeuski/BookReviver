import { useMemo, useState } from 'react';
import type { AppliesTo, ProcessorSchema, RecipeSchema, Stage } from '@/api';
import type { PreviewRequest } from '@/features/processing/preview';
import { useProcessors, useRecipes } from '@/features/processing/queries';
import {
  addStep,
  bodyOf,
  canPreview,
  draftOf,
  moveStep,
  removeStep,
  type StepDraft,
  sameAsSaved,
  setStepCondition,
  setStepParams,
  toggleStep,
} from '@/features/processing/recipe';
import { fitsSchema, formSchemaOf } from '@/features/processing/schema';
import { type PreviewResult, usePreview } from '@/features/processing/usePreview';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The state of the processing panel of a stage: the catalogue of its processors, its recipes and the one being looked
 * at, the draft of the steps the reader is editing, and the preview of that draft on the open page.
 *
 * The draft belongs to the saved recipe it was made from. When the recipe is saved or changed on the server it has a new
 * `updated_at`, and the draft is made again from it, so what the panel shows is never a draft of a recipe that is gone.
 * The panel and the canvas both read this, so a preview asked for by the panel is drawn by the canvas.
 */

/** The preview of the draft and what it needs to be asked for. */
export interface PreviewControl extends PreviewResult {
  /** Whether the reader asked for the preview. */
  on: boolean;
  toggle: () => void;
  /** Why the preview cannot be asked for now, or null when it can. */
  blocked: PreviewBlock | null;
}

/** Why there is no preview to ask for. */
export const PreviewBlock = {
  NoPage: 'no-page',
  NoStep: 'no-step',
  Invalid: 'invalid',
  Split: 'split',
} as const;

/** One reason of {@link PreviewBlock}. */
export type PreviewBlock = (typeof PreviewBlock)[keyof typeof PreviewBlock];

/** The steps of the stage panel as the screen reads and changes them. */
export interface Processing {
  projectId: string;
  stage: Stage;
  /** Whether the stage is built from processors, which is when the catalogue has one of this stage. */
  available: boolean;
  /** Whether the catalogue and the recipes have been read. */
  ready: boolean;
  failed: boolean;
  /** The processors of this stage. */
  catalogue: readonly ProcessorSchema[];
  /** Every recipe of the stage, the active one first. */
  recipes: readonly RecipeSchema[];
  /** The recipe the panel shows. */
  recipe: RecipeSchema | undefined;
  chooseRecipe: (id: string) => void;
  steps: readonly StepDraft[];
  /** The step that is open, whose settings are drawn. */
  openId: string | undefined;
  open: (id: string | undefined) => void;
  /**
   * The step of the recipe, by its index, whose result the canvas and "This page" show, or null for the result the stage
   * stands on, which is the last step a page was run through.
   */
  shownStep: number | null;
  showStep: (index: number | null) => void;
  /** Whether the draft differs from the saved recipe. */
  dirty: boolean;
  /** Whether every value of the draft fits the schema of its processor. */
  valid: boolean;
  move: (activeId: string, overId: string) => void;
  toggle: (id: string) => void;
  remove: (id: string) => void;
  change: (id: string, params: Record<string, unknown>) => void;
  /** Change which pages a step processes. */
  condition: (id: string, appliesTo: AppliesTo) => void;
  add: (processor: ProcessorSchema) => void;
  discard: () => void;
  preview: PreviewControl;
}

/** Index of the step whose result a preview is wanted of: the open one, else the last. */
function previewIndex(steps: readonly StepDraft[], openId: string | undefined): number {
  const found = steps.findIndex((step) => step.id === openId);
  return found >= 0 ? found : steps.length - 1;
}

/**
 * Read the processing state of a stage.
 *
 * @param projectId The book.
 * @param stage The stage.
 * @param current The page open on the canvas, which a preview is made of.
 * @param onPreviewStart Called when the reader turns the preview on, so the canvas can show the picture after.
 */
export function useProcessing(
  projectId: string,
  stage: Stage,
  current: StripItem | undefined,
  onPreviewStart: () => void,
): Processing {
  const processors = useProcessors();
  const catalogue = useMemo(
    () => (processors.data ?? []).filter((processor) => processor.stage === stage),
    [processors.data, stage],
  );
  const available = catalogue.length > 0;
  const recipes = useRecipes(projectId, stage, available);

  const [chosenId, setChosenId] = useState<{ stage: Stage; id: string } | null>(null);
  const list = recipes.data ?? [];
  const recipe =
    list.find((entry) => entry.id === (chosenId?.stage === stage ? chosenId.id : undefined)) ??
    list[0];

  const draftOwner = recipe === undefined ? '' : `${recipe.id}@${recipe.updated_at}`;
  const [edit, setEdit] = useState<{ owner: string; steps: StepDraft[] } | null>(null);
  const steps = useMemo(
    () => (edit?.owner === draftOwner ? edit.steps : recipe === undefined ? [] : draftOf(recipe)),
    [edit, draftOwner, recipe],
  );
  const [openChoice, setOpenChoice] = useState<{
    owner: string | undefined;
    id: string | undefined;
  } | null>(null);
  // The step that is open stays open when the recipe it belongs to is saved or measured, which only moves its time; the
  // steps keep their places, so the reader sees the new numbers in the form they were looking at
  const openId = openChoice?.owner === recipe?.id ? openChoice?.id : steps[0]?.id;
  // The step whose result is shown is an index, so it belongs to the saved recipe it was chosen in, as the draft does
  const [shownChoice, setShownChoice] = useState<{ owner: string; index: number | null } | null>(
    null,
  );
  const shownStep = shownChoice?.owner === draftOwner ? shownChoice.index : null;

  const schemas = useMemo(
    () =>
      new Map(catalogue.map((processor) => [processor.key, formSchemaOf(processor.parameters)])),
    [catalogue],
  );
  const valid = steps.every((step) => {
    const schema = schemas.get(step.processorKey);
    return schema === undefined || fitsSchema(schema, step.params);
  });
  const dirty = recipe !== undefined && !sameAsSaved(recipe, steps);

  const [previewOn, setPreviewOn] = useState(false);
  const index = previewIndex(steps, openId);
  let blocked: PreviewBlock | null = null;
  if (current === undefined) {
    blocked = PreviewBlock.NoPage;
  } else if (!valid) {
    blocked = PreviewBlock.Invalid;
  } else if (!canPreview(steps, catalogue, index)) {
    blocked = steps
      .slice(0, index + 1)
      .some(
        (step) =>
          step.enabled &&
          catalogue.find((entry) => entry.key === step.processorKey)?.scope === 'split',
      )
      ? PreviewBlock.Split
      : PreviewBlock.NoStep;
  }
  const request: PreviewRequest | null =
    blocked === null && current !== undefined
      ? { pageId: current.page.id, steps: bodyOf(steps), stepIndex: index }
      : null;
  const result = usePreview(projectId, stage, request, previewOn && blocked === null);

  const write = (next: StepDraft[]): void => setEdit({ owner: draftOwner, steps: next });

  return {
    projectId,
    stage,
    available,
    ready: !available || (recipes.data !== undefined && recipe !== undefined),
    failed: processors.isError || recipes.isError,
    catalogue,
    recipes: list,
    recipe,
    chooseRecipe: (id) => setChosenId({ stage, id }),
    steps,
    openId,
    open: (id) => setOpenChoice({ owner: recipe?.id, id }),
    shownStep,
    showStep: (index) => setShownChoice({ owner: draftOwner, index }),
    dirty,
    valid,
    move: (activeId, overId) => write(moveStep(steps, activeId, overId)),
    toggle: (id) => write(toggleStep(steps, id)),
    remove: (id) => write(removeStep(steps, id)),
    change: (id, params) => write(setStepParams(steps, id, params)),
    condition: (id, appliesTo) => write(setStepCondition(steps, id, appliesTo)),
    add: (processor) => {
      const next = addStep(steps, processor);
      write(next);
      setOpenChoice({ owner: recipe?.id, id: next.at(-1)?.id });
    },
    discard: () => setEdit(null),
    preview: {
      ...result,
      on: previewOn,
      toggle: () => {
        if (!previewOn) {
          onPreviewStart();
        }
        setPreviewOn(!previewOn);
      },
      blocked,
    },
  };
}
