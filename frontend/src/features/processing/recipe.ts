import { arrayMove } from '@dnd-kit/sortable';
import type { AppliesTo, ProcessorSchema, RecipeSchema, StagePageSchema, StepBody } from '@/api';
import { withMarginsSource } from '@/features/processing/margins';
import { defaultsOf, formSchemaOf } from '@/features/processing/schema';

/**
 * The steps of a recipe as the panel edits them: a draft that the reader changes without saving, the operations on it,
 * and the questions the panel asks of it.
 *
 * A recipe may name a processor twice, so a draft step carries an `id` that the list can be dragged by. A saved step also
 * has an identifier the server gave it, which its manual edits are kept under and which a save sends back, so the step
 * keeps its edits as it is moved. A step added to the draft has none until the recipe is saved.
 */

/** One step of the recipe being edited. */
export interface StepDraft {
  /** Identity inside the draft, for the list to drag and to key by. */
  id: string;
  /** The identifier of the saved step, or null for a step that was added to the draft. */
  stepId: string | null;
  processorKey: string;
  params: Record<string, unknown>;
  enabled: boolean;
  /** Which pages the step processes; the others pass it unchanged. */
  appliesTo: AppliesTo;
}

/** The conditions a step can have, in the order the select of a step lists them. */
export const CONDITIONS: readonly AppliesTo[] = [
  'all',
  'text',
  'pictures',
  'color-pictures',
  'bw-pictures',
];

const ID_PREFIX = 'step-';

function idNumber(id: string): number {
  return id.startsWith(ID_PREFIX) ? Number(id.slice(ID_PREFIX.length)) : -1;
}

/** Start a draft from the saved steps of a recipe. */
export function draftOf(recipe: Pick<RecipeSchema, 'steps'>): StepDraft[] {
  return recipe.steps.map((step, index) => ({
    id: `${ID_PREFIX}${index}`,
    stepId: step.step_id,
    processorKey: step.processor_key,
    params: { ...step.params },
    enabled: step.enabled,
    appliesTo: step.applies_to,
  }));
}

/** Give the steps as the API takes them, in a recipe to save or in a preview. */
export function bodyOf(steps: readonly StepDraft[]): StepBody[] {
  return steps.map((step) => ({
    processor_key: step.processorKey,
    params: step.params,
    enabled: step.enabled,
    step_id: step.stepId,
    applies_to: step.appliesTo,
  }));
}

/** Put the step `activeId` where the step `overId` stands, which is what a drop on it asks for. */
export function moveStep(
  steps: readonly StepDraft[],
  activeId: string,
  overId: string,
): StepDraft[] {
  const from = steps.findIndex((step) => step.id === activeId);
  const to = steps.findIndex((step) => step.id === overId);
  return from < 0 || to < 0 || from === to ? [...steps] : arrayMove([...steps], from, to);
}

/** Switch a step on or off, which keeps its parameters. */
export function toggleStep(steps: readonly StepDraft[], id: string): StepDraft[] {
  return steps.map((step) => (step.id === id ? { ...step, enabled: !step.enabled } : step));
}

/** Change which pages a step processes, which keeps its parameters. */
export function setStepCondition(
  steps: readonly StepDraft[],
  id: string,
  appliesTo: AppliesTo,
): StepDraft[] {
  return steps.map((step) => (step.id === id ? { ...step, appliesTo } : step));
}

/** Take a step out of the draft. */
export function removeStep(steps: readonly StepDraft[], id: string): StepDraft[] {
  return steps.filter((step) => step.id !== id);
}

/**
 * Replace the parameters of a step with what its form holds. A change of a margin of the normalize step also marks its
 * margins as set by hand, so the next measure of the book leaves them.
 */
export function setStepParams(
  steps: readonly StepDraft[],
  id: string,
  params: Record<string, unknown>,
): StepDraft[] {
  return steps.map((step) =>
    step.id === id
      ? { ...step, params: withMarginsSource(step.processorKey, step.params, params) }
      : step,
  );
}

/**
 * Add a step of a processor, with the defaults of its parameter schema.
 *
 * @param steps The draft.
 * @param processor The processor of the catalogue to add.
 * @param index The place of the new step, from zero, which is the end when it is not given.
 */
export function addStep(
  steps: readonly StepDraft[],
  processor: ProcessorSchema,
  index: number = steps.length,
): StepDraft[] {
  const next = steps.reduce((highest, step) => Math.max(highest, idNumber(step.id)), -1) + 1;
  const added: StepDraft = {
    id: `${ID_PREFIX}${next}`,
    stepId: null,
    processorKey: processor.key,
    params: defaultsOf(formSchemaOf(processor.parameters)),
    enabled: true,
    appliesTo: 'all',
  };
  return [...steps.slice(0, index), added, ...steps.slice(index)];
}

/** Tell whether two JSON values are equal, whatever the order of the keys of the objects in them. */
export function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, index) => sameValue(item, b[index]))
    );
  }
  if (typeof a === 'object' && a !== null && typeof b === 'object' && b !== null) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((key) =>
      sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]),
    );
  }
  return a === b;
}

/** Tell whether the draft holds the same steps, in the same order with the same values, as the saved recipe. */
export function sameAsSaved(
  recipe: Pick<RecipeSchema, 'steps'>,
  steps: readonly StepDraft[],
): boolean {
  return sameValue(bodyOf(draftOf(recipe)), bodyOf(steps));
}

/**
 * Count the pages whose result a save of the recipe would make out of date: those it has processed and that are up to
 * date now.
 *
 * @param rows The rows of the stage.
 * @param recipeId The recipe being saved.
 */
export function pagesToGoStale(rows: readonly StagePageSchema[], recipeId: string): number {
  return rows.filter((row) => row.recipe_id === recipeId && row.status === 'fresh').length;
}

/**
 * Tell whether the steps up to one of them can be previewed on a page: a step that cuts a scan into pages cannot,
 * since a preview makes one image of a page, and there must be a step that is on.
 *
 * @param steps The draft.
 * @param catalogue The processors of the stage.
 * @param index The step whose result is wanted.
 */
export function canPreview(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
  index: number,
): boolean {
  const wanted = steps.slice(0, index + 1).filter((step) => step.enabled);
  return (
    wanted.length > 0 &&
    wanted.every((step) => {
      const processor = catalogue.find((entry) => entry.key === step.processorKey);
      return processor !== undefined && processor.scope === 'page';
    })
  );
}
