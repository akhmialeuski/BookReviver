import { useMemo, useState } from 'react';
import type { OrderMode, ProcessorSchema, RecipeKind, RecipeSchema, Stage } from '@/api';
import {
  issuesByStep,
  type OrderIssue,
  orderIssues,
  refusalOf,
  restoreUsualOrder,
} from '@/features/processing/order';
import { useProcessors, useRecipes } from '@/features/processing/queries';
import {
  addStep,
  draftOf,
  moveStep,
  removeStep,
  type StepDraft,
  sameAsSaved,
  setStepParams,
  toggleStep,
} from '@/features/processing/recipe';
import { fitsSchema, formSchemaOf } from '@/features/processing/schema';

/**
 * The state of the processing panel of a stage: the catalogue of its processors, its recipes and the one being looked
 * at, and the draft of the steps the reader is editing.
 *
 * The draft belongs to the saved recipe it was made from. When the recipe is saved or changed on the server it has a new
 * `updated_at`, and the draft is made again from it, so what the panel shows is never a draft of a recipe that is gone.
 */

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
  /** Every recipe of the stage, one for each kind of page, in the order of the kinds. */
  recipes: readonly RecipeSchema[];
  /** The recipe the panel shows. */
  recipe: RecipeSchema | undefined;
  chooseRecipe: (id: string) => void;
  steps: readonly StepDraft[];
  /** The step that is open, whose settings are drawn. */
  openId: string | undefined;
  open: (id: string | undefined) => void;
  /** Whether the draft differs from the saved recipe. */
  dirty: boolean;
  /** Whether every value of the draft fits the schema of its processor. */
  valid: boolean;
  /** The order the draft is saved in: the usual one refuses a step where it cannot work, the free one only warns. */
  orderMode: OrderMode;
  setOrderMode: (mode: OrderMode) => void;
  /** The steps that stand off the place their processors ask for, by the identity of the step in the draft. */
  orderIssues: ReadonlyMap<string, readonly OrderIssue[]>;
  /** The places that keep the draft from being saved in the usual order, none in the free order. */
  refused: readonly OrderIssue[];
  /** The required place that dropping a step on another would break, if any. */
  refusalOf: (activeId: string, overId: string) => OrderIssue | undefined;
  /** Put the steps in their usual order, which keeps every setting. */
  restoreOrder: () => void;
  /** Put other steps on the screen, as the steps of a profile are, with the order they are saved in. */
  loadSteps: (steps: StepDraft[], mode: OrderMode) => void;
  move: (activeId: string, overId: string) => void;
  toggle: (id: string) => void;
  remove: (id: string) => void;
  change: (id: string, params: Record<string, unknown>) => void;
  add: (processor: ProcessorSchema) => void;
  discard: () => void;
}

/** A recipe the reader chose in the picker, with the place it was chosen at. */
export interface RecipeChoice {
  stage: Stage;
  id: string;
  /** The step the address named when the choice was made. */
  stepId: string | undefined;
  /** The kind of the page that was open when the choice was made. */
  kind: RecipeKind | undefined;
}

/**
 * Pick the recipe the panel shows.
 *
 * A page is processed by the recipe of its kind, so the panel shows the recipe of the open page, and an edit of a step
 * lands in the recipe that processes the page. A recipe chosen in the picker outranks it until a page of another kind
 * is opened, and while the address names a step of that recipe or the step it named when the choice was made. Before
 * the rows of the pages are read, the recipe that owns the step of the address is shown, and without one the first.
 *
 * @param recipes Every recipe of the stage.
 * @param stage The stage.
 * @param stepId The step the address names, or undefined.
 * @param pageKind The kind of the open page, or undefined while it is not known.
 * @param choice The recipe chosen in the picker, or null.
 * @returns The recipe shown, or undefined when the stage has none.
 */
export function shownRecipe(
  recipes: readonly RecipeSchema[],
  stage: Stage,
  stepId: string | undefined,
  pageKind: RecipeKind | undefined,
  choice: RecipeChoice | null,
): RecipeSchema | undefined {
  const owner =
    stepId === undefined
      ? undefined
      : recipes.find((entry) => entry.steps.some((step) => step.step_id === stepId));
  const chosen =
    choice?.stage === stage &&
    choice.kind === pageKind &&
    (owner === undefined || owner.id === choice.id || choice.stepId === stepId)
      ? recipes.find((entry) => entry.id === choice.id)
      : undefined;
  const ofKind =
    pageKind === undefined ? undefined : recipes.find((entry) => entry.kind === pageKind);
  return chosen ?? ofKind ?? owner ?? recipes[0];
}

/**
 * Read the processing state of a stage.
 *
 * The recipe shown is picked by `shownRecipe`: the recipe of the open page's kind, unless the reader chose another.
 *
 * @param projectId The book.
 * @param stage The stage.
 * @param stepId The step the address names, or undefined when it names none.
 * @param pageKind The kind of the open page, or undefined while it is not known.
 */
export function useProcessing(
  projectId: string,
  stage: Stage,
  stepId?: string,
  pageKind?: RecipeKind,
): Processing {
  const processors = useProcessors();
  const catalogue = useMemo(
    () => (processors.data ?? []).filter((processor) => processor.stage === stage),
    [processors.data, stage],
  );
  const available = catalogue.length > 0;
  const recipes = useRecipes(projectId, stage, available);

  const [choice, setChoice] = useState<RecipeChoice | null>(null);
  const list = recipes.data ?? [];
  const recipe = shownRecipe(list, stage, stepId, pageKind, choice);

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
  // A recipe that was saved in the free order, with a step where it cannot work, is opened in the free order, since the
  // usual one would refuse the next save of it
  const [modeChoice, setModeChoice] = useState<{
    owner: string | undefined;
    mode: OrderMode;
  } | null>(null);
  const savedFree = recipe?.order_issues?.some((issue) => issue.kind === 'required') ?? false;
  const orderMode: OrderMode =
    modeChoice !== null && modeChoice.owner === recipe?.id
      ? modeChoice.mode
      : savedFree
        ? 'free'
        : 'usual';
  const issues = useMemo(() => orderIssues(steps, catalogue), [steps, catalogue]);
  const issuesOf = useMemo(() => issuesByStep(issues), [issues]);
  const refused = orderMode === 'free' ? [] : issues.filter((issue) => issue.kind === 'required');

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
    chooseRecipe: (id) => setChoice({ stage, id, stepId, kind: pageKind }),
    steps,
    openId,
    open: (id) => setOpenChoice({ owner: recipe?.id, id }),
    dirty,
    valid,
    orderMode,
    setOrderMode: (mode) => setModeChoice({ owner: recipe?.id, mode }),
    orderIssues: issuesOf,
    refused,
    refusalOf: (activeId, overId) => refusalOf(steps, catalogue, activeId, overId),
    restoreOrder: () => write(restoreUsualOrder(steps, catalogue)),
    loadSteps: (next, mode) => {
      write(next);
      setModeChoice({ owner: recipe?.id, mode });
    },
    move: (activeId, overId) => write(moveStep(steps, activeId, overId)),
    toggle: (id) => write(toggleStep(steps, id)),
    remove: (id) => write(removeStep(steps, id)),
    change: (id, params) => write(setStepParams(steps, id, params)),
    add: (processor) => {
      const next = addStep(steps, processor);
      write(next);
      setOpenChoice({ owner: recipe?.id, id: next.at(-1)?.id });
    },
    discard: () => setEdit(null),
  };
}
